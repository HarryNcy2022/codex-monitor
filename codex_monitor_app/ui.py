import json
import threading
import time
import tkinter as tk
import urllib.error
from datetime import datetime
from tkinter import ttk
from typing import Optional

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
    def __init__(self, root: tk.Tk):
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
        self._active_combobox: Optional[ttk.Combobox] = None
        self._auto_fetch_editor_email: Optional[str] = None

        self.setup_ui()
        self.refresh_ui()

        self.auth_watcher.start()
        self.root.after(0, self.initial_fetch_on_startup)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_ui(self) -> None:
        style = ttk.Style()
        style.configure("Codex.Treeview", rowheight=32, font=("Arial", 11))
        style.configure("Codex.Treeview.Heading", font=("Arial", 11, "bold"))
        style.configure("AutoFetch.TCombobox", font=("Arial", 11))
        style.map(
            "Codex.Treeview",
            background=[("selected", "#F5F5F5")],
            foreground=[("selected", "#202124")],
        )

        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        top_frame = tk.Frame(self.root)
        top_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(5, 0))

        manual_button = ttk.Button(
            top_frame,
            text="Manual Fetch (Current auth.json)",
            command=self.manual_fetch,
        )
        manual_button.pack(side=tk.LEFT)

        frame = tk.Frame(self.root)
        frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        columns = ("Email", "Quota Left", "Reset Time", "Auto-Fetch")
        self.tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            style="Codex.Treeview",
            selectmode="none",
            takefocus=False,
        )
        self.tree.heading("Email", text="Account Email")
        self.tree.heading("Quota Left", text="Quota Left")
        self.tree.heading("Reset Time", text="Reset Time")
        self.tree.heading("Auto-Fetch", text="Auto-Fetch ▾")

        self.tree.column("Email", width=300)
        self.tree.column("Quota Left", width=100, anchor=tk.CENTER)
        self.tree.column("Reset Time", width=260, anchor=tk.CENTER)
        self.tree.column("Auto-Fetch", width=125, anchor=tk.CENTER)

        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.tag_configure(
            "current_account",
            background="#E8FFF1",
            foreground="#0F6B3C",
            font=("Arial", 11, "bold"),
        )
        self.tree.bind(
            "<<TreeviewSelect>>",
            lambda event: self.tree.selection_remove(self.tree.selection()),
        )
        self.tree.bind("<Configure>", lambda event: self.refresh_auto_fetch_editor())

        self.status_var = tk.StringVar()
        self.status_var.set(f"Watching: {AUTH_FILE_PATH} (watchdog)")

        bg_color = self.root.cget("background")
        status_label = tk.Entry(
            self.root,
            textvariable=self.status_var,
            fg="gray",
            font=("Arial", 10),
            bd=0,
            readonlybackground=bg_color,
            highlightthickness=0,
            state="readonly",
        )
        status_label.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 5))

    def _destroy_active_combobox(self) -> None:
        if self._active_combobox and self._active_combobox.winfo_exists():
            self._active_combobox.destroy()
        self._active_combobox = None
        self._auto_fetch_editor_email = None

    def save_auto_fetch_value(self, email: str, new_value: str) -> None:
        if self.state.save_auto_fetch_value(email, new_value):
            self.refresh_ui()

    def refresh_auto_fetch_editor(self) -> None:
        email = self.state.current_account_email
        if not email or email not in self.state.usage_map:
            self._destroy_active_combobox()
            return

        try:
            x, y, width, height = self.tree.bbox(email, "#4")
        except tk.TclError:
            self._destroy_active_combobox()
            return

        if not width or not height:
            self._destroy_active_combobox()
            return

        pad_x = 3
        pad_y = 2

        if (
            not self._active_combobox
            or not self._active_combobox.winfo_exists()
            or self._auto_fetch_editor_email != email
        ):
            self._destroy_active_combobox()
            combobox = ttk.Combobox(
                self.tree,
                values=AUTO_FETCH_OPTIONS,
                state="readonly",
                style="AutoFetch.TCombobox",
            )
            combobox.bind(
                "<<ComboboxSelected>>",
                lambda event, account_email=email, widget=combobox: (
                    self.save_auto_fetch_value(account_email, widget.get())
                ),
            )
            self._active_combobox = combobox
            self._auto_fetch_editor_email = email

        self._active_combobox.set(
            self.state.usage_map[email].get("auto_fetch", "None")
        )
        self._active_combobox.place(
            x=x + pad_x,
            y=y + pad_y,
            width=max(width - (pad_x * 2), 40),
            height=max(height - (pad_y * 2), 24),
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
                self.status_var.set(
                    f"Logout detected. Taking final snapshot for {email}..."
                )
                threading.Thread(
                    target=self._final_snapshot_and_clear,
                    args=(email, jwt),
                    daemon=True,
                ).start()
            else:
                if self.state.clear_session_credentials():
                    self._destroy_active_combobox()
                    self.root.after_idle(self.refresh_ui)
                self.status_var.set("auth.json was removed. Logged out from Codex.")
            return

        try:
            jwt = self.auth_file_service.load_access_token()
            if jwt:
                threading.Thread(
                    target=self._bg_fetch_single,
                    args=(None, jwt),
                    daemon=True,
                ).start()
            else:
                if self.state.clear_session_credentials():
                    self._destroy_active_combobox()
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
            return

        try:
            response = self._fetch_usage(jwt)
            email = self.state.apply_usage_response(response, jwt)
            if email:
                self.root.after(0, self.refresh_ui)
                message = f"Successfully updated quota for {email}"
                self.root.after(0, lambda m=message: self.status_var.set(m))
            else:
                message = "Warning: Could not find email or reset_at in API response"
                self.root.after(0, lambda m=message: self.status_var.set(m))
                print(f"[Safe Error Log] {message}")

        except urllib.error.HTTPError as error:
            message = f"HTTP Error {error.code} - Token might be expired"
            print(f"[Safe Error Log] {message}")
            self.root.after(0, lambda m=message: self.status_var.set(m))

        except (urllib.error.URLError, TimeoutError) as error:
            message = f"Network Error: {getattr(error, 'reason', str(error))}"
            if "CERTIFICATE_VERIFY_FAILED" in str(getattr(error, "reason", "")):
                message = (
                    "SSL Error: Run 'Install Certificates.command' in Mac Python "
                    "folder, or run 'pip install certifi'"
                )

            print(f"[Safe Error Log] {message}")
            self.root.after(0, lambda m=message: self.status_var.set(m))

        except Exception as error:
            message = f"Unknown error: {str(error)}"
            print(f"[Safe Error Log] Exception triggered: {message}")
            self.root.after(0, lambda m=message: self.status_var.set(m))

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
            self._destroy_active_combobox()
            self.status_var.set(message)
            self.refresh_ui()

    def check_auto_fetch(self) -> None:
        jwt = self.state.get_due_auto_fetch_jwt(time.time())
        if jwt:
            threading.Thread(
                target=self._bg_fetch_single,
                args=(self.state.current_account_email, jwt),
                daemon=True,
            ).start()

    def refresh_ui(self) -> None:
        if self._timer_id:
            self.root.after_cancel(self._timer_id)

        self.check_auto_fetch()

        for item in self.tree.get_children():
            self.tree.delete(item)

        now_ts = datetime.now().timestamp()
        for email, data in self.state.sorted_usage_items():
            try:
                reset_ts = data.get("reset_ts", 0)
                used_percent = data.get("used_percent", 0)
                auto_fetch = self.state.get_display_auto_fetch(email)
                is_current = email == self.state.current_account_email
                email_display = f"{email}   [CURRENT]" if is_current else email

                display_text = format_reset_display(reset_ts, now_ts)
                quota_left = format_quota_left(used_percent)

                self.tree.insert(
                    "",
                    tk.END,
                    iid=email,
                    values=(email_display, quota_left, display_text, auto_fetch),
                    tags=("current_account",) if is_current else (),
                )
            except Exception:
                self.tree.insert("", tk.END, iid=email, values=(email, "Error", "Error", "-"))

        self.root.after_idle(self.refresh_auto_fetch_editor)
        self._timer_id = self.root.after(60000, self.refresh_ui)

    def on_closing(self) -> None:
        if self._timer_id:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None

        self.auth_watcher.stop()
        self.root.destroy()
