import json
import subprocess
import threading
import time
import tkinter as tk
import urllib.error
from datetime import datetime
from typing import Dict, Optional

import customtkinter as ctk

from .api import UsageApiClient
from .config import (
    APP_VERSION,
    APP_TITLE,
    AUTH_DIR,
    AUTH_FILE_PATH,
    AUTO_FETCH_OPTIONS,
    UPDATE_CHECK_INTERVAL_SECONDS,
    WINDOW_GEOMETRY,
    WINDOW_MIN_SIZE,
)
from .formatters import format_quota_left, format_reset_display
from .services import AuthFileService, MonitorStateService
from .storage import UsageStorage
from .updater import (
    ReleaseInfo,
    UpdateError,
    fetch_latest_release,
    install_update_and_restart,
    is_newer_version,
    prepare_update,
)
from .watcher import AuthFileWatcher


class CodexMonitorApp:
    TABLE_HEADER_PAD_Y = 6
    TABLE_ROW_PAD_Y = 5
    TABLE_ROW_GAP_Y = 4
    TABLE_SCROLLBAR_WIDTH = 8
    TABLE_SCROLLBAR_PAD_X = 4
    AUTH_EVENT_SETTLE_MS = 250
    AUTH_PARSE_RETRY_MS = 300
    MAX_AUTH_PARSE_RETRIES = 4

    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(WINDOW_GEOMETRY)
        self.root.minsize(*WINDOW_MIN_SIZE)

        self.storage = UsageStorage()
        self.api_client = UsageApiClient()
        self.state = MonitorStateService(self.storage)
        self.auth_file_service = AuthFileService()
        self.auth_watcher = AuthFileWatcher(
            watch_dir=AUTH_DIR,
            target_file=AUTH_FILE_PATH,
            callback=self.on_file_changed,
        )

        self._timer_id: Optional[str] = None
        self._pending_fetches = 0
        self._accounts_window_id: Optional[int] = None
        self._update_check_timer_id: Optional[str] = None
        self._auth_change_job: Optional[str] = None
        self._auth_retry_job: Optional[str] = None
        self._auth_retry_attempts = 0
        self.manual_button: Optional[ctk.CTkButton] = None
        self.copy_status_button: Optional[ctk.CTkButton] = None
        self.auto_fetch_label: Optional[ctk.CTkLabel] = None
        self.auto_fetch_menu: Optional[ctk.CTkOptionMenu] = None
        self.status_textbox: Optional[tk.Text] = None
        self.update_button: Optional[ctk.CTkButton] = None
        self.theme_button: Optional[ctk.CTkButton] = None
        self._available_release: Optional[ReleaseInfo] = None
        self._update_check_in_progress = False
        self._update_in_progress = False

        self.setup_ui()
        self.refresh_ui()

        self.auth_watcher.start()
        self.root.after(0, self.initial_fetch_on_startup)
        self.root.after(1200, self.check_for_updates_silently)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _theme_tokens(self) -> Dict[str, str]:
        if ctk.get_appearance_mode().lower() == "dark":
            return {
                "app_bg": "#0E141B",
                "card": "#16202A",
                "text": "#ECF3F8",
                "muted": "#97A6B3",
                "border": "#263341",
                "table_shell": "#121A23",
                "table_fg": "#ECF3F8",
                "header_bg": "#1A2633",
                "header_fg": "#F5FAFF",
                "selection_bg": "#234C84",
                "control_bg": "#3B82F6",
                "control_hover": "#60A5FA",
                "control_fg": "#F8FBFF",
                "scrollbar_thumb": "#344352",
                "scrollbar_thumb_hover": "#516273",
                "row_even": "#131D27",
                "row_odd": "#1A2530",
                "row_border": "#263341",
                "current_bg": "#123126",
                "current_fg": "#E4FAEC",
                "current_border": "#2D8A66",
                "empty_fg": "#91A0AE",
            }

        return {
            "app_bg": "#F3F7FB",
            "card": "#FFFFFF",
            "text": "#10202F",
            "muted": "#5F7283",
            "border": "#D7E1EB",
            "table_shell": "#FFFFFF",
            "table_fg": "#10202F",
            "header_bg": "#E8F0F7",
            "header_fg": "#10202F",
            "selection_bg": "#C9DAFF",
            "control_bg": "#2563EB",
            "control_hover": "#1D4ED8",
            "control_fg": "#FFFFFF",
            "scrollbar_thumb": "#B5C2CF",
            "scrollbar_thumb_hover": "#7D90A3",
            "row_even": "#FFFFFF",
            "row_odd": "#F1F6FB",
            "row_border": "#DCE5EE",
            "current_bg": "#E4F6EC",
            "current_fg": "#12613F",
            "current_border": "#87C8A8",
            "empty_fg": "#748697",
        }

    def setup_ui(self) -> None:
        tokens = self._theme_tokens()
        self.root.configure(fg_color=tokens["app_bg"])
        self.root.unbind_all("<MouseWheel>")
        self.root.unbind_all("<Button-4>")
        self.root.unbind_all("<Button-5>")

        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        accounts_shell = ctk.CTkFrame(
            self.root,
            corner_radius=18,
            fg_color=tokens["table_shell"],
            border_width=1,
            border_color=tokens["border"],
        )
        accounts_shell.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 6))
        accounts_shell.grid_columnconfigure(0, weight=1)
        accounts_shell.grid_rowconfigure(1, weight=1)
        self.accounts_shell = accounts_shell

        header_frame = ctk.CTkFrame(
            accounts_shell,
            corner_radius=12,
            fg_color=tokens["header_bg"],
            border_width=1,
            border_color=tokens["border"],
        )
        header_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 3))
        self._configure_account_columns(header_frame)
        header_frame.grid_columnconfigure(
            3,
            minsize=self._table_scrollbar_gutter_width(),
        )
        self._build_header_cell(header_frame, "Account Email", 0, "w")
        self._build_header_cell(header_frame, "Quota", 1, "w")
        self._build_header_cell(header_frame, "Reset Time", 2, "w")

        body_frame = ctk.CTkFrame(
            accounts_shell,
            corner_radius=10,
            fg_color="transparent",
        )
        body_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=(0, 5))
        body_frame.grid_columnconfigure(0, weight=1)
        body_frame.grid_rowconfigure(0, weight=1)

        self.accounts_canvas = tk.Canvas(
            body_frame,
            highlightthickness=0,
            bd=0,
            relief="flat",
            background=tokens["table_shell"],
        )
        self.accounts_canvas.grid(row=0, column=0, sticky="nsew")
        self.accounts_canvas.configure(yscrollincrement=6)

        accounts_scrollbar = ctk.CTkScrollbar(
            body_frame,
            orientation="vertical",
            command=self.accounts_canvas.yview,
            width=self.TABLE_SCROLLBAR_WIDTH,
            corner_radius=999,
            border_spacing=0,
            minimum_pixel_length=36,
            fg_color=tokens["table_shell"],
            button_color=tokens["scrollbar_thumb"],
            button_hover_color=tokens["scrollbar_thumb_hover"],
        )
        accounts_scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
            padx=(self.TABLE_SCROLLBAR_PAD_X, 0),
        )
        self.accounts_canvas.configure(yscrollcommand=accounts_scrollbar.set)

        self.accounts_rows_frame = ctk.CTkFrame(
            self.accounts_canvas,
            fg_color="transparent",
        )
        self.accounts_rows_frame.grid_columnconfigure(0, weight=1)
        self._accounts_window_id = self.accounts_canvas.create_window(
            (0, 0),
            window=self.accounts_rows_frame,
            anchor="nw",
        )
        self.accounts_rows_frame.bind(
            "<Configure>",
            lambda event: self._update_accounts_scrollregion(),
        )
        self.accounts_canvas.bind("<Configure>", self._resize_accounts_window)

        self.root.bind_all("<MouseWheel>", self._on_global_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_global_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self._on_global_mousewheel, add="+")

        status_frame = ctk.CTkFrame(
            self.root,
            corner_radius=16,
            fg_color=tokens["card"],
            border_width=1,
            border_color=tokens["border"],
        )
        status_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        status_frame.grid_columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value=f"Watching: {AUTH_FILE_PATH} (watchdog)")
        self.status_var.trace_add("write", self._sync_status_textbox)
        self.status_textbox = tk.Text(
            status_frame,
            height=2,
            wrap="word",
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
            background=tokens["card"],
            foreground=tokens["muted"],
            insertbackground=tokens["text"],
            selectbackground=tokens["selection_bg"],
            font=("TkDefaultFont", 11),
        )
        self.status_textbox.grid(row=0, column=0, sticky="ew", padx=(12, 0), pady=9)

        self.copy_status_button = ctk.CTkButton(
            status_frame,
            text="⧉",
            command=self.copy_status_message,
            corner_radius=11,
            height=34,
            width=34,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=tokens["control_bg"],
            hover_color=tokens["control_hover"],
            text_color=tokens["control_fg"],
        )
        self.copy_status_button.grid(row=0, column=1, sticky="w", padx=(0, 10), pady=7)

        self.auto_fetch_label = ctk.CTkLabel(
            status_frame,
            text="Auto Fetch",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=tokens["muted"],
        )
        self.auto_fetch_label.grid(row=0, column=2, sticky="e", padx=(0, 5), pady=7)

        self.auto_fetch_menu = ctk.CTkOptionMenu(
            status_frame,
            values=AUTO_FETCH_OPTIONS,
            command=self.save_current_auto_fetch_value,
            width=88,
            height=32,
            corner_radius=10,
            dynamic_resizing=False,
            font=ctk.CTkFont(size=11, weight="bold"),
            dropdown_font=ctk.CTkFont(size=11),
            fg_color=tokens["control_bg"],
            button_color=tokens["control_bg"],
            button_hover_color=tokens["control_hover"],
            text_color=tokens["control_fg"],
            anchor="w",
        )
        self.auto_fetch_menu.grid(row=0, column=3, sticky="e", padx=(0, 6), pady=7)

        self.manual_button = ctk.CTkButton(
            status_frame,
            text=self._fetch_button_icon(),
            command=self.manual_fetch,
            corner_radius=11,
            height=34,
            width=34,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=tokens["control_bg"],
            hover_color=tokens["control_hover"],
            text_color=tokens["control_fg"],
        )
        self.manual_button.grid(row=0, column=4, sticky="e", padx=(0, 6), pady=7)

        self.update_button = ctk.CTkButton(
            status_frame,
            text="Update",
            command=self.update_application,
            corner_radius=11,
            height=34,
            width=82,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=tokens["control_bg"],
            hover_color=tokens["control_hover"],
            text_color=tokens["control_fg"],
        )
        self.update_button.grid(row=0, column=5, sticky="e", padx=(0, 6), pady=7)

        self.theme_button = ctk.CTkButton(
            status_frame,
            text=self._appearance_toggle_icon(),
            command=self.toggle_appearance_mode,
            corner_radius=11,
            height=34,
            width=34,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=tokens["control_bg"],
            hover_color=tokens["control_hover"],
            text_color=tokens["control_fg"],
        )
        self.theme_button.grid(row=0, column=6, sticky="e", padx=(0, 10), pady=7)
        self._sync_status_textbox()
        self._update_manual_button_state()

    def _configure_account_columns(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=6, uniform="account-cols")
        frame.grid_columnconfigure(1, weight=2, uniform="account-cols")
        frame.grid_columnconfigure(2, weight=4, uniform="account-cols")

    def _table_scrollbar_gutter_width(self) -> int:
        return self.TABLE_SCROLLBAR_WIDTH + self.TABLE_SCROLLBAR_PAD_X

    def _fetch_button_icon(self) -> str:
        return "↻"

    def _appearance_toggle_icon(self) -> str:
        if ctk.get_appearance_mode().lower() == "dark":
            return "☀"
        return "☾"

    def rebuild_ui(self) -> None:
        status_message = self.status_var.get() if hasattr(self, "status_var") else ""
        for widget in self.root.winfo_children():
            widget.destroy()
        self.manual_button = None
        self.copy_status_button = None
        self.auto_fetch_label = None
        self.auto_fetch_menu = None
        self.status_textbox = None
        self.update_button = None
        self.theme_button = None
        self._accounts_window_id = None
        self.setup_ui()
        if status_message:
            self.status_var.set(status_message)
        self._update_manual_button_state()
        self.refresh_ui(skip_auto_fetch=True)

    def toggle_appearance_mode(self) -> None:
        next_mode = (
            "light" if ctk.get_appearance_mode().lower() == "dark" else "dark"
        )
        ctk.set_appearance_mode(next_mode)
        self.rebuild_ui()

    def _build_header_cell(
        self,
        parent: ctk.CTkFrame,
        text: str,
        column: int,
        anchor: str,
    ) -> None:
        tokens = self._theme_tokens()
        label = ctk.CTkLabel(
            parent,
            text=text,
            anchor=anchor,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=tokens["header_fg"],
        )
        label.grid(
            row=0,
            column=column,
            sticky="ew",
            padx=10,
            pady=self.TABLE_HEADER_PAD_Y,
        )

    def _build_value_label(
        self,
        parent: ctk.CTkFrame,
        text: str,
        text_color: str,
        column: int,
        anchor: str = "w",
        bold: bool = False,
    ) -> None:
        label = ctk.CTkLabel(
            parent,
            text=text,
            anchor=anchor,
            font=ctk.CTkFont(size=11, weight="bold" if bold else "normal"),
            text_color=text_color,
        )
        label.grid(
            row=0,
            column=column,
            sticky="ew",
            padx=10,
            pady=self.TABLE_ROW_PAD_Y,
        )

    def _clear_account_rows(self) -> None:
        for widget in self.accounts_rows_frame.winfo_children():
            widget.destroy()

    def _is_widget_in_accounts_area(self, widget: Optional[tk.Misc]) -> bool:
        while widget is not None:
            if widget in (
                self.accounts_canvas,
                self.accounts_rows_frame,
                self.accounts_shell,
            ):
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_global_mousewheel(self, event: tk.Event) -> Optional[str]:
        hovered_widget = self.root.winfo_containing(
            self.root.winfo_pointerx(),
            self.root.winfo_pointery(),
        )
        if not self._is_widget_in_accounts_area(hovered_widget):
            return None

        pixel_delta = 0.0
        event_num = getattr(event, "num", None)

        if event_num == 4:
            pixel_delta = -24.0
        elif event_num == 5:
            pixel_delta = 24.0
        else:
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return "break"
            if abs(delta) >= 120:
                pixel_delta = -(delta / 120.0) * 48.0
            else:
                pixel_delta = -delta * 2.5

        self._scroll_accounts_by_pixels(pixel_delta)
        return "break"

    def _scroll_accounts_by_pixels(self, pixel_delta: float) -> None:
        bbox = self.accounts_canvas.bbox("all")
        if not bbox:
            return

        widget_height = max(self.accounts_canvas.winfo_height(), 1)
        content_height = max(bbox[3] - bbox[1], widget_height)
        max_first = max(1.0 - (widget_height / content_height), 0.0)
        if max_first <= 0:
            return

        first, _ = self.accounts_canvas.yview()
        next_first = min(
            max(first + (pixel_delta / content_height), 0.0),
            max_first,
        )
        self.accounts_canvas.yview_moveto(next_first)

    def _resize_accounts_window(self, event: tk.Event) -> None:
        if self._accounts_window_id is not None:
            self.accounts_canvas.itemconfigure(self._accounts_window_id, width=event.width)

    def _update_accounts_scrollregion(self) -> None:
        bbox = self.accounts_canvas.bbox("all")
        self.accounts_canvas.configure(scrollregion=bbox or (0, 0, 0, 0))

    def _sync_status_textbox(self, *_args: object) -> None:
        if not self.status_textbox or not hasattr(self, "status_var"):
            return

        message = self.status_var.get()
        self.status_textbox.configure(state="normal")
        self.status_textbox.delete("1.0", "end")
        self.status_textbox.insert("1.0", message)
        self.status_textbox.configure(state="disabled")

    def copy_status_message(self) -> None:
        message = self.status_var.get() if hasattr(self, "status_var") else ""
        if not message:
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(message)

    def _schedule_next_update_check(self) -> None:
        if self._update_check_timer_id:
            self.root.after_cancel(self._update_check_timer_id)

        self._update_check_timer_id = self.root.after(
            UPDATE_CHECK_INTERVAL_SECONDS * 1000,
            self.check_for_updates_silently,
        )

    def _set_update_button_visibility(self) -> None:
        if not self.update_button or not self.theme_button:
            return

        show_button = self._available_release is not None or self._update_in_progress
        if show_button:
            self.update_button.grid()
            self.theme_button.grid_configure(column=6)
        else:
            self.update_button.grid_remove()
            self.theme_button.grid_configure(column=5)

    def _update_manual_button_state(self) -> None:
        if not self.manual_button:
            return

        if self._pending_fetches > 0:
            self.manual_button.configure(state="disabled", text="…")
        else:
            self.manual_button.configure(state="normal", text=self._fetch_button_icon())

        if self.update_button:
            if self._update_in_progress:
                self.update_button.configure(state="disabled", text="...")
            elif self._available_release:
                self.update_button.configure(state="normal", text="Update")
            else:
                self.update_button.configure(state="normal", text="Update")

        current_email = self.state.current_account_email
        if self.auto_fetch_menu:
            if current_email:
                self.auto_fetch_menu.configure(state="normal")
                self.auto_fetch_menu.set(self.state.get_display_auto_fetch(current_email))
            else:
                self.auto_fetch_menu.configure(state="disabled")
                self.auto_fetch_menu.set("None")

        if self.auto_fetch_label:
            self.auto_fetch_label.configure(
                text_color=self._theme_tokens()["muted"],
            )

        self._set_update_button_visibility()

        if self.theme_button:
            self.theme_button.configure(text=self._appearance_toggle_icon())

    def _begin_fetch(self, status_message: str) -> None:
        self._pending_fetches += 1
        self.status_var.set(status_message)
        self._update_manual_button_state()

    def _finish_fetch(self, status_message: str) -> None:
        self._pending_fetches = max(self._pending_fetches - 1, 0)
        self.status_var.set(status_message)
        self._update_manual_button_state()

    def save_auto_fetch_value(self, email: str, new_value: str) -> None:
        if self.state.save_auto_fetch_value(email, new_value):
            self.refresh_ui()
            self.status_var.set(f"Auto-fetch for {email} set to {new_value}.")

    def save_current_auto_fetch_value(self, new_value: str) -> None:
        email = self.state.current_account_email
        if not email:
            if self.auto_fetch_menu:
                self.auto_fetch_menu.set("None")
            self.status_var.set("No active account to attach auto-fetch to.")
            return

        self.save_auto_fetch_value(email, new_value)

    def _build_account_row(
        self,
        email: str,
        quota_left: str,
        reset_display: str,
        is_current: bool,
        index: int,
    ) -> None:
        tokens = self._theme_tokens()
        row_bg = tokens["current_bg"] if is_current else (
            tokens["row_even"] if index % 2 == 0 else tokens["row_odd"]
        )
        row_text = tokens["current_fg"] if is_current else tokens["table_fg"]
        email_display = f"{email}   ACTIVE" if is_current else email

        row = ctk.CTkFrame(
            self.accounts_rows_frame,
            fg_color=row_bg,
            corner_radius=12,
            border_width=1,
            border_color=tokens["current_border"] if is_current else tokens["row_border"],
        )
        row.grid(
            row=index,
            column=0,
            sticky="ew",
            padx=1,
            pady=self.TABLE_ROW_GAP_Y,
        )
        self._configure_account_columns(row)

        self._build_value_label(
            row,
            email_display,
            row_text,
            0,
            anchor="w",
            bold=is_current,
        )
        self._build_value_label(
            row,
            quota_left,
            row_text,
            1,
            anchor="w",
            bold=is_current,
        )
        self._build_value_label(
            row,
            reset_display,
            row_text,
            2,
            anchor="w",
        )

    def initial_fetch_on_startup(self) -> None:
        self.status_var.set("Checking current auth.json on startup...")
        self.process_auth_file()

    def on_file_changed(self) -> None:
        self.root.after(0, self._schedule_auth_refresh)

    def _schedule_auth_refresh(self) -> None:
        if self._auth_retry_job:
            self.root.after_cancel(self._auth_retry_job)
            self._auth_retry_job = None

        if self._auth_change_job:
            self.root.after_cancel(self._auth_change_job)

        self._auth_change_job = self.root.after(
            self.AUTH_EVENT_SETTLE_MS,
            self._handle_file_changed,
        )

    def _handle_file_changed(self) -> None:
        self._auth_change_job = None
        self._auth_retry_attempts = 0
        self.status_var.set("Auth file changed. Fetching new quota...")
        self.process_auth_file()

    def _schedule_auth_parse_retry(self) -> None:
        if self._auth_retry_attempts >= self.MAX_AUTH_PARSE_RETRIES:
            self.status_var.set(
                "auth.json is still invalid. Waiting for the next file change."
            )
            self._auth_retry_job = None
            return

        self._auth_retry_attempts += 1
        self.status_var.set(
            f"auth.json is mid-write. Retrying read ({self._auth_retry_attempts}/{self.MAX_AUTH_PARSE_RETRIES})..."
        )
        self._auth_retry_job = self.root.after(
            self.AUTH_PARSE_RETRY_MS,
            self._retry_process_auth_file,
        )

    def _retry_process_auth_file(self) -> None:
        self._auth_retry_job = None
        self.process_auth_file()

    def _reset_auth_retry_state(self) -> None:
        self._auth_retry_attempts = 0
        if self._auth_retry_job:
            self.root.after_cancel(self._auth_retry_job)
            self._auth_retry_job = None

    def manual_fetch(self) -> None:
        self.status_var.set("Manual fetch initiated...")
        self.process_auth_file()

    def check_for_updates_silently(self) -> None:
        if self._update_check_in_progress or self._update_in_progress:
            return

        self._update_check_in_progress = True
        threading.Thread(target=self._bg_check_for_updates, daemon=True).start()

    def _bg_check_for_updates(self) -> None:
        release: Optional[ReleaseInfo] = None
        error_message: Optional[str] = None

        try:
            latest_release = fetch_latest_release()
            if is_newer_version(latest_release.version, APP_VERSION):
                release = latest_release
        except urllib.error.HTTPError as error:
            error_message = f"Update check failed with HTTP {error.code}."
        except (urllib.error.URLError, TimeoutError) as error:
            error_message = f"Network Error while checking for updates: {getattr(error, 'reason', str(error))}"
        except UpdateError as error:
            error_message = f"Update check error: {error}"
        except Exception as error:
            error_message = f"Unexpected update check error: {error}"

        self.root.after(
            0,
            lambda r=release, e=error_message: self._finish_update_check(r, e),
        )

    def _finish_update_check(
        self,
        release: Optional[ReleaseInfo],
        error_message: Optional[str],
    ) -> None:
        self._update_check_in_progress = False
        if error_message:
            resolved_release = self._available_release
        else:
            resolved_release = release

        self._available_release = resolved_release

        if resolved_release:
            current_message = self.status_var.get()
            if (
                "Update available:" in current_message
                or current_message.startswith("Watching: ")
            ):
                self.status_var.set(
                    f"Update available: v{resolved_release.version}. Tap the button to install."
                )
        elif error_message and self.status_var.get().startswith("Watching: "):
            self.status_var.set(error_message)

        self._update_manual_button_state()
        self._schedule_next_update_check()

    def update_application(self) -> None:
        if self._update_in_progress or not self._available_release:
            return

        self._update_in_progress = True
        self.status_var.set(
            f"Installing v{self._available_release.version} from v{APP_VERSION}..."
        )
        self._update_manual_button_state()
        threading.Thread(target=self._bg_update_application, daemon=True).start()

    def _bg_update_application(self) -> None:
        should_close = False

        try:
            if not self._available_release:
                message = f"You're already on the latest version (v{APP_VERSION})."
            else:
                release = self._available_release
                source_app, target_app, temp_root = prepare_update(release)
                install_update_and_restart(source_app, target_app, temp_root)
                message = (
                    f"Installing v{release.version} and reopening from {target_app}..."
                )
                should_close = True
        except urllib.error.HTTPError as error:
            message = f"Update check failed with HTTP {error.code}."
        except (urllib.error.URLError, TimeoutError) as error:
            message = f"Network Error while updating: {getattr(error, 'reason', str(error))}"
        except subprocess.CalledProcessError:
            message = "Update download succeeded, but extracting the app failed."
        except UpdateError as error:
            message = f"Update error: {error}"
        except Exception as error:
            message = f"Unexpected update error: {error}"

        self.root.after(
            0,
            lambda m=message, close_after=should_close: self._finish_update(
                m,
                close_after,
            ),
        )

    def _finish_update(self, status_message: str, close_after: bool = False) -> None:
        self._update_in_progress = False
        if close_after:
            self._available_release = None
        self.status_var.set(status_message)
        self._update_manual_button_state()
        if close_after:
            self.root.after(500, self.on_closing)

    def process_auth_file(self) -> None:
        if not self.auth_file_service.auth_file_exists():
            self._reset_auth_retry_state()
            email = self.state.current_account_email
            jwt = self.state.get_latest_jwt_for_fetch(email)

            if jwt:
                account_label = email or "current account"
                self._begin_fetch(
                    f"Logout detected. Taking final snapshot for {account_label}..."
                )
                threading.Thread(
                    target=self._final_snapshot_and_clear,
                    args=(email, jwt),
                    daemon=True,
                ).start()
            else:
                if self.state.clear_session_credentials():
                    self.root.after_idle(self.refresh_ui)
                self.status_var.set("auth.json was removed. Logged out from Codex.")
            return

        try:
            jwt = self.auth_file_service.load_access_token()
            if jwt:
                self._reset_auth_retry_state()
                self.state.remember_auth_jwt(jwt)
                self._begin_fetch("Fetching quota from current auth.json...")
                threading.Thread(
                    target=self._bg_fetch_single,
                    args=(None, jwt),
                    daemon=True,
                ).start()
            else:
                self._reset_auth_retry_state()
                email = self.state.current_account_email
                latest_jwt = self.state.get_latest_jwt_for_fetch(email)
                if latest_jwt:
                    account_label = email or "current account"
                    self._begin_fetch(
                        f"Logout detected. Taking final snapshot for {account_label}..."
                    )
                    threading.Thread(
                        target=self._final_snapshot_and_clear,
                        args=(email, latest_jwt),
                        daemon=True,
                    ).start()
                else:
                    if self.state.clear_session_credentials():
                        self.root.after_idle(self.refresh_ui)
                    self.status_var.set(
                        "No access_token found in auth.json. Logged out from Codex."
                    )
        except json.JSONDecodeError:
            self._schedule_auth_parse_retry()
        except Exception as error:
            self._reset_auth_retry_state()
            self.status_var.set(f"Failed to parse auth.json: {error}")

    def _fetch_usage(self, jwt: str) -> dict:
        return self.api_client.fetch_usage(jwt)

    def _bg_fetch_single(self, expected_email: Optional[str], jwt: str) -> None:
        del expected_email
        if not jwt:
            self.root.after(0, lambda: self._finish_fetch("No token available for fetch."))
            return

        try:
            response = self._fetch_usage(jwt)
            email = self.state.apply_usage_response(response, jwt)
            if email:
                self.root.after(0, self.refresh_ui)
                message = f"Successfully updated quota for {email}."
            else:
                message = "Warning: Could not find email or reset_at in API response."
                print(f"[Safe Error Log] {message}")

        except urllib.error.HTTPError as error:
            message = f"HTTP Error {error.code}. Token may be expired."
            print(f"[Safe Error Log] {message}")

        except (urllib.error.URLError, TimeoutError) as error:
            message = f"Network Error: {getattr(error, 'reason', str(error))}"
            if "CERTIFICATE_VERIFY_FAILED" in str(getattr(error, "reason", "")):
                message = (
                    "SSL Error: Run 'Install Certificates.command' in Mac Python "
                    "folder, or run 'pip install certifi'."
                )

            print(f"[Safe Error Log] {message}")

        except Exception as error:
            message = f"Unknown error: {str(error)}"
            print(f"[Safe Error Log] Exception triggered: {message}")

        self.root.after(0, lambda m=message: self._finish_fetch(m))

    def _final_snapshot_and_clear(self, email: Optional[str], jwt: str) -> None:
        account_label = email or "current account"
        try:
            response = self._fetch_usage(jwt)
            updated_email = self.state.apply_usage_response(response, jwt)
            if updated_email:
                message = (
                    f"Logged out. Final snapshot successfully saved for {updated_email}."
                )
            else:
                message = (
                    f"Logged out. Could not parse final snapshot for {account_label}."
                )
        except urllib.error.HTTPError as error:
            if error.code == 401:
                message = (
                    "Logged out. Token instantly revoked, kept last known quota for "
                    f"{account_label}."
                )
            else:
                message = f"Logged out. HTTP {error.code} during final fetch."
        except Exception as error:
            message = f"Logged out. Error during final fetch: {str(error)}"

        self.root.after(0, lambda m=message: self._finalize_logout(m))

    def _finalize_logout(self, message: str) -> None:
        if not self.auth_file_service.auth_file_exists():
            self.state.clear_session_credentials()
            self.refresh_ui()
        self._finish_fetch(message)

    def check_auto_fetch(self) -> None:
        jwt = self.state.get_due_auto_fetch_jwt(time.time())
        if jwt:
            email = self.state.current_account_email or "current account"
            self._begin_fetch(f"Auto-fetching quota for {email}...")
            threading.Thread(
                target=self._bg_fetch_single,
                args=(self.state.current_account_email, jwt),
                daemon=True,
            ).start()

    def refresh_ui(self, skip_auto_fetch: bool = False) -> None:
        if self._timer_id:
            self.root.after_cancel(self._timer_id)

        if not skip_auto_fetch:
            self.check_auto_fetch()
        self._clear_account_rows()

        items = self.state.sorted_usage_items()
        if not items:
            empty_label = ctk.CTkLabel(
                self.accounts_rows_frame,
                text="No tracked accounts yet. Fetch current auth.json to load one.",
                font=ctk.CTkFont(size=12),
                text_color=self._theme_tokens()["empty_fg"],
            )
            empty_label.grid(row=0, column=0, sticky="ew", padx=10, pady=20)
        else:
            now_ts = datetime.now().timestamp()
            for index, (email, data) in enumerate(items):
                try:
                    reset_ts = data.get("reset_ts", 0)
                    used_percent = data.get("used_percent", 0)
                    is_current = email == self.state.current_account_email
                    display_text = format_reset_display(reset_ts, now_ts)
                    quota_left = format_quota_left(used_percent)
                except Exception:
                    display_text = "Error"
                    quota_left = "Error"
                    is_current = email == self.state.current_account_email

                self._build_account_row(
                    email=email,
                    quota_left=quota_left,
                    reset_display=display_text,
                    is_current=is_current,
                    index=index,
                )

        self.root.after_idle(self._update_accounts_scrollregion)
        self._timer_id = self.root.after(60000, self.refresh_ui)

    def on_closing(self) -> None:
        if self._timer_id:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None
        if self._update_check_timer_id:
            self.root.after_cancel(self._update_check_timer_id)
            self._update_check_timer_id = None

        self.auth_watcher.stop()
        self.root.destroy()
