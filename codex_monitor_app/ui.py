import json
import threading
import time
import tkinter as tk
import urllib.error
from datetime import datetime
from typing import Dict, Optional

import customtkinter as ctk

from .api import UsageApiClient
from .config import (
    APP_TITLE,
    AUTH_DIR,
    AUTH_FILE_PATH,
    AUTO_FETCH_OPTIONS,
    WINDOW_GEOMETRY,
    WINDOW_MIN_SIZE,
)
from .formatters import format_quota_left, format_reset_display
from .services import AuthFileService, MonitorStateService
from .storage import UsageStorage
from .watcher import AuthFileWatcher


class CodexMonitorApp:
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
        self._last_file_event_time = 0.0
        self._pending_fetches = 0
        self._accounts_window_id: Optional[int] = None
        self.manual_button: Optional[ctk.CTkButton] = None
        self.theme_button: Optional[ctk.CTkButton] = None

        self.setup_ui()
        self.refresh_ui()

        self.auth_watcher.start()
        self.root.after(0, self.initial_fetch_on_startup)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _theme_tokens(self) -> Dict[str, str]:
        if ctk.get_appearance_mode().lower() == "dark":
            return {
                "app_bg": "#09111E",
                "card": "#101A2B",
                "card_alt": "#0F172A",
                "text": "#F8FAFC",
                "muted": "#94A3B8",
                "border": "#1E293B",
                "table_shell": "#0B1425",
                "table_fg": "#E2E8F0",
                "heading_bg": "#132238",
                "heading_fg": "#F8FAFC",
                "selection_bg": "#1D4ED8",
                "row_even": "#0F172A",
                "row_odd": "#111D33",
                "current_bg": "#DCFCE7",
                "current_fg": "#166534",
                "empty_fg": "#CBD5E1",
            }

        return {
            "app_bg": "#F3F7FB",
            "card": "#FFFFFF",
            "card_alt": "#F8FAFC",
            "text": "#0F172A",
            "muted": "#475569",
            "border": "#D7E2EE",
            "table_shell": "#EEF4FA",
            "table_fg": "#0F172A",
            "heading_bg": "#E1EDF7",
            "heading_fg": "#0F172A",
            "selection_bg": "#CFE3F8",
            "row_even": "#FFFFFF",
            "row_odd": "#F7FAFD",
            "current_bg": "#DCFCE7",
            "current_fg": "#166534",
            "empty_fg": "#64748B",
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
            corner_radius=14,
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
            corner_radius=10,
            fg_color=tokens["heading_bg"],
        )
        header_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 3))
        self._configure_account_columns(header_frame)
        self._build_header_cell(header_frame, "Account Email", 0, "w")
        self._build_header_cell(header_frame, "Quota Left", 1, "w")
        self._build_header_cell(header_frame, "Reset Time", 2, "w")
        self._build_header_cell(header_frame, "Auto-Fetch", 3, "w")

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
            fg_color=tokens["table_shell"],
            button_color=tokens["heading_bg"],
            button_hover_color=tokens["selection_bg"],
        )
        accounts_scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
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
            corner_radius=12,
            fg_color=tokens["card_alt"],
            border_width=1,
            border_color=tokens["border"],
        )
        status_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        status_frame.grid_columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value=f"Watching: {AUTH_FILE_PATH} (watchdog)")
        status_label = ctk.CTkLabel(
            status_frame,
            textvariable=self.status_var,
            anchor="w",
            justify="left",
            font=ctk.CTkFont(size=11),
            text_color=tokens["muted"],
        )
        status_label.grid(row=0, column=0, sticky="ew", padx=(12, 10), pady=9)

        self.manual_button = ctk.CTkButton(
            status_frame,
            text=self._fetch_button_icon(),
            command=self.manual_fetch,
            corner_radius=11,
            height=34,
            width=34,
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self.manual_button.grid(row=0, column=1, sticky="e", padx=(0, 6), pady=7)

        self.theme_button = ctk.CTkButton(
            status_frame,
            text=self._appearance_toggle_icon(),
            command=self.toggle_appearance_mode,
            corner_radius=11,
            height=34,
            width=34,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=tokens["heading_bg"],
            hover_color=tokens["selection_bg"],
            text_color=tokens["heading_fg"],
        )
        self.theme_button.grid(row=0, column=2, sticky="e", padx=(0, 10), pady=7)
        self._update_manual_button_state()

    def _configure_account_columns(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=5, uniform="account-cols")
        frame.grid_columnconfigure(1, weight=2, uniform="account-cols")
        frame.grid_columnconfigure(2, weight=4, uniform="account-cols")
        frame.grid_columnconfigure(3, weight=3, uniform="account-cols")

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
            text_color=tokens["heading_fg"],
        )
        label.grid(row=0, column=column, sticky="ew", padx=10, pady=9)

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
        label.grid(row=0, column=column, sticky="ew", padx=10, pady=8)

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

    def _update_manual_button_state(self) -> None:
        if not self.manual_button:
            return

        if self._pending_fetches > 0:
            self.manual_button.configure(state="disabled", text="…")
        else:
            self.manual_button.configure(state="normal", text=self._fetch_button_icon())

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

    def _build_account_row(
        self,
        email: str,
        quota_left: str,
        reset_display: str,
        auto_fetch: str,
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
            corner_radius=10,
        )
        row.grid(row=index, column=0, sticky="ew", padx=1, pady=2)
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

        if is_current:
            auto_fetch_menu = ctk.CTkOptionMenu(
                row,
                values=AUTO_FETCH_OPTIONS,
                command=lambda choice, account_email=email: (
                    self.save_auto_fetch_value(account_email, choice)
                ),
                width=136,
                height=28,
                corner_radius=9,
                dynamic_resizing=False,
                font=ctk.CTkFont(size=11, weight="bold"),
                dropdown_font=ctk.CTkFont(size=11),
                fg_color=tokens["heading_bg"],
                button_color=tokens["heading_bg"],
                button_hover_color=tokens["selection_bg"],
                text_color=tokens["heading_fg"],
                anchor="w",
            )
            auto_fetch_menu.set(auto_fetch)
            auto_fetch_menu.grid(row=0, column=3, sticky="w", padx=10, pady=5)
        else:
            self._build_value_label(
                row,
                "-",
                row_text,
                3,
                anchor="w",
            )

    def initial_fetch_on_startup(self) -> None:
        self.status_var.set("Checking current auth.json on startup...")
        self.process_auth_file()

    def on_file_changed(self) -> None:
        current_time = time.monotonic()
        if current_time - self._last_file_event_time < 1.0:
            return

        self._last_file_event_time = current_time
        self.root.after(0, self._handle_file_changed)

    def _handle_file_changed(self) -> None:
        self.status_var.set("Auth file changed. Fetching new quota...")
        self.process_auth_file()

    def manual_fetch(self) -> None:
        self.status_var.set("Manual fetch initiated...")
        self.process_auth_file()

    def process_auth_file(self) -> None:
        if not self.auth_file_service.auth_file_exists():
            email = self.state.current_account_email
            jwt = self.state.session_tokens.get(email) if email else None

            if email and jwt:
                self._begin_fetch(f"Logout detected. Taking final snapshot for {email}...")
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
                self._begin_fetch("Fetching quota from current auth.json...")
                threading.Thread(
                    target=self._bg_fetch_single,
                    args=(None, jwt),
                    daemon=True,
                ).start()
            else:
                if self.state.clear_session_credentials():
                    self.root.after_idle(self.refresh_ui)
                self.status_var.set(
                    "No access_token found in auth.json. Logged out from Codex."
                )
        except json.JSONDecodeError:
            self.status_var.set(
                "auth.json is empty or invalid JSON. Retrying after the next file change."
            )
        except Exception as error:
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

    def _final_snapshot_and_clear(self, email: str, jwt: str) -> None:
        try:
            response = self._fetch_usage(jwt)
            updated_email = self.state.apply_usage_response(response, jwt)
            if updated_email:
                message = f"Logged out. Final snapshot successfully saved for {email}."
            else:
                message = f"Logged out. Could not parse final snapshot for {email}."
        except urllib.error.HTTPError as error:
            if error.code == 401:
                message = (
                    f"Logged out. Token instantly revoked, kept last known quota for {email}."
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
                    auto_fetch = self.state.get_display_auto_fetch(email)
                    is_current = email == self.state.current_account_email
                    display_text = format_reset_display(reset_ts, now_ts)
                    quota_left = format_quota_left(used_percent)
                except Exception:
                    display_text = "Error"
                    quota_left = "Error"
                    auto_fetch = "-"
                    is_current = email == self.state.current_account_email

                self._build_account_row(
                    email=email,
                    quota_left=quota_left,
                    reset_display=display_text,
                    auto_fetch=auto_fetch,
                    is_current=is_current,
                    index=index,
                )

        self.root.after_idle(self._update_accounts_scrollregion)
        self._timer_id = self.root.after(60000, self.refresh_ui)

    def on_closing(self) -> None:
        if self._timer_id:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None

        self.auth_watcher.stop()
        self.root.destroy()
