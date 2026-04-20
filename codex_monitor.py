import os
import json
import time
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk
from datetime import datetime
import threading
import ssl
import certifi
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Automatically resolves to /Users/<your_username>/.codex/auth.json
AUTH_FILE_PATH = os.path.expanduser('~/.codex/auth.json')
AUTH_DIR = os.path.dirname(AUTH_FILE_PATH)

# Lightweight local storage equivalent
LOCAL_STORAGE_FILE = os.path.expanduser('~/.codex_usage_store.json')

# API URL
USAGE_API_URL = 'https://chatgpt.com/backend-api/wham/usage'

# API RESPONSE SAMPLE (DUN REMOVE, USEFUL FOR ENHANCE FURTHER)

"""
{
  "user_id": "user-xxxxxxxxxxxxxxxxxxxx",
  "account_id": "user-xxxxxxxxxxxxxxxxxxxx",
  "email": "meme@gmail.com",
  "plan_type": "free",
  "rate_limit": {
    "allowed": true,
    "limit_reached": false,
    "primary_window": {
      "used_percent": 0,
      "limit_window_seconds": 604800,
      "reset_after_seconds": 604800,
      "reset_at": 1775575453
    },
    "secondary_window": null
  },
  "code_review_rate_limit": {
    "allowed": true,
    "limit_reached": false,
    "primary_window": {
      "used_percent": 0,
      "limit_window_seconds": 604800,
      "reset_after_seconds": 604800,
      "reset_at": 1775575453
    },
    "secondary_window": null
  },
  "additional_rate_limits": null,
  "credits": {
    "has_credits": false,
    "unlimited": false,
    "balance": null,
    "approx_local_messages": null,
    "approx_cloud_messages": null
  },
  "spend_control": {
    "reached": false
  },
  "promo": null
}
"""


class AuthFileHandler(FileSystemEventHandler):
    def __init__(self, callback, target_file):
        self.callback = callback
        self.target_file = os.path.abspath(target_file)

    def _matches(self, path):
        return path and os.path.abspath(path) == self.target_file

    def on_modified(self, event):
        if not event.is_directory and self._matches(event.src_path):
            self.callback()

    def on_created(self, event):
        if not event.is_directory and self._matches(event.src_path):
            self.callback()

    def on_deleted(self, event):
        if not event.is_directory and self._matches(event.src_path):
            self.callback()

    def on_moved(self, event):
        if not event.is_directory and (self._matches(event.src_path) or self._matches(event.dest_path)):
            self.callback()


class CodexMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Codex Account Monitor")
        self.root.geometry("850x350")
        self.root.minsize(700, 250)

        self.usage_map = self.load_data()
        self.session_tokens = {}
        self.current_account_email = None
        self._timer_id = None
        self._last_file_event_time = 0.0
        self._active_combobox = None
        self._auto_fetch_editor_email = None
        self.observer = None

        self.setup_ui()
        self.refresh_ui()

        self.start_watchdog()
        self.root.after(0, self.initial_fetch_on_startup)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_ui(self):
        style = ttk.Style()
        style.configure("Codex.Treeview", rowheight=32, font=("Arial", 11))
        style.configure("Codex.Treeview.Heading", font=("Arial", 11, "bold"))
        style.configure("AutoFetch.TCombobox", font=("Arial", 11))
        style.map(
            "Codex.Treeview",
            background=[("selected", "#F5F5F5")],
            foreground=[("selected", "#202124")]
        )

        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        # Top frame for buttons
        top_frame = tk.Frame(self.root)
        top_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(5, 0))

        btn_manual = ttk.Button(top_frame, text="Manual Fetch (Current auth.json)", command=self.manual_fetch)
        btn_manual.pack(side=tk.LEFT)

        # Main Table frame
        frame = tk.Frame(self.root)
        frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        columns = ('Email', 'Quota Left', 'Reset Time', 'Time Until Reset', 'Auto-Fetch')
        self.tree = ttk.Treeview(
            frame,
            columns=columns,
            show='headings',
            style="Codex.Treeview",
            selectmode="none",
            takefocus=False
        )
        self.tree.heading('Email', text='Account Email')
        self.tree.heading('Quota Left', text='Quota Left')
        self.tree.heading('Reset Time', text='Reset Time')
        self.tree.heading('Time Until Reset', text='Time Until Reset')
        self.tree.heading('Auto-Fetch', text='Auto-Fetch ▾')

        self.tree.column('Email', width=300)
        self.tree.column('Quota Left', width=100, anchor=tk.CENTER)
        self.tree.column('Reset Time', width=180, anchor=tk.CENTER)
        self.tree.column('Time Until Reset', width=130, anchor=tk.CENTER)
        self.tree.column('Auto-Fetch', width=125, anchor=tk.CENTER)

        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.tag_configure("current_account", background="#E8FFF1", foreground="#0F6B3C", font=("Arial", 11, "bold"))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.tree.selection_remove(self.tree.selection()))
        self.tree.bind("<Configure>", lambda e: self.refresh_auto_fetch_editor())

        # Status Bar
        self.status_var = tk.StringVar()
        self.status_var.set(f"Watching: {AUTH_FILE_PATH} (watchdog)")

        # Get the default window background color so it blends in perfectly
        bg_color = self.root.cget("background")

        status_label = tk.Entry(
            self.root,
            textvariable=self.status_var,
            fg="gray",
            font=("Arial", 10),
            bd=0,  # No border
            readonlybackground=bg_color,  # Match window background
            highlightthickness=0,
            state="readonly"  # Makes it copyable but not editable
        )
        status_label.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 5))

    def load_data(self):
        data = {}
        needs_resave = False
        if os.path.exists(LOCAL_STORAGE_FILE):
            try:
                with open(LOCAL_STORAGE_FILE, 'r') as f:
                    raw = json.load(f)
                    # Migrate old schema {email: reset_ts} to new dict schema
                    for k, v in raw.items():
                        if isinstance(v, (int, float)):
                            data[k] = {
                                "reset_ts": v, "used_percent": 0,
                                "auto_fetch": "None", "last_fetched": 0
                            }
                            needs_resave = True
                        else:
                            sanitized = dict(v)
                            if "jwt" in sanitized:
                                sanitized.pop("jwt", None)
                                needs_resave = True
                            data[k] = sanitized
            except Exception:
                pass
        if needs_resave:
            self._save_sanitized_data(data)
        return data

    def _save_sanitized_data(self, data):
        sanitized_data = {}
        for email, account_data in data.items():
            clean_account = dict(account_data)
            clean_account.pop("jwt", None)
            sanitized_data[email] = clean_account

        with open(LOCAL_STORAGE_FILE, 'w') as f:
            json.dump(sanitized_data, f)

    def save_data(self):
        try:
            self._save_sanitized_data(self.usage_map)
        except Exception as e:
            print(f"Failed to save data: {e}")

    def clear_session_credentials(self):
        had_current_account = self.current_account_email is not None or bool(self.session_tokens)
        self.session_tokens.clear()
        self.current_account_email = None
        self._destroy_active_combobox()

        if had_current_account:
            self.root.after_idle(self.refresh_ui)

    def set_current_account(self, email, jwt):
        self.current_account_email = email
        self.session_tokens = {email: jwt}

        changed = False
        for account_email, data in self.usage_map.items():
            if account_email != email and data.get("auto_fetch", "None") != "None":
                data["auto_fetch"] = "None"
                changed = True

        if changed:
            self.save_data()

    def get_display_auto_fetch(self, email, data):
        if email != self.current_account_email:
            return "-"
        return data.get("auto_fetch", "None")

    # --- TREEVIEW COMBOBOX (DROPDOWN) LOGIC ---
    def _destroy_active_combobox(self):
        if self._active_combobox and self._active_combobox.winfo_exists():
            self._active_combobox.destroy()
        self._active_combobox = None
        self._auto_fetch_editor_email = None

    def save_auto_fetch_value(self, email, new_val):
        if email in self.usage_map and email == self.current_account_email:
            self.usage_map[email]["auto_fetch"] = new_val
            self.save_data()
            self.refresh_ui()

    def refresh_auto_fetch_editor(self):
        email = self.current_account_email
        if not email or email not in self.usage_map:
            self._destroy_active_combobox()
            return

        try:
            x, y, w, h = self.tree.bbox(email, "#5")
        except tk.TclError:
            self._destroy_active_combobox()
            return

        if not w or not h:
            self._destroy_active_combobox()
            return

        pad_x = 3
        pad_y = 2

        if not self._active_combobox or not self._active_combobox.winfo_exists() or self._auto_fetch_editor_email != email:
            self._destroy_active_combobox()
            cb = ttk.Combobox(
                self.tree,
                values=["None", "1 Hr", "3 Hrs", "12 Hrs", "24 Hrs"],
                state="readonly",
                style="AutoFetch.TCombobox"
            )
            cb.bind("<<ComboboxSelected>>", lambda e, account_email=email, widget=cb: self.save_auto_fetch_value(account_email, widget.get()))
            self._active_combobox = cb
            self._auto_fetch_editor_email = email

        self._active_combobox.set(self.usage_map[email].get("auto_fetch", "None"))
        self._active_combobox.place(
            x=x + pad_x,
            y=y + pad_y,
            width=max(w - (pad_x * 2), 40),
            height=max(h - (pad_y * 2), 24)
        )

    # --- BACKGROUND WATCHER & FETCH LOGIC ---
    def start_watchdog(self):
        os.makedirs(AUTH_DIR, exist_ok=True)
        event_handler = AuthFileHandler(self.on_file_changed, AUTH_FILE_PATH)
        self.observer = Observer()
        self.observer.schedule(event_handler, AUTH_DIR, recursive=False)
        self.observer.start()

    def initial_fetch_on_startup(self):
        self.status_var.set("Checking current auth.json on startup...")
        self.process_auth_file()

    def on_file_changed(self):
        current_time = time.monotonic()
        if current_time - self._last_file_event_time < 1.0:
            return

        self._last_file_event_time = current_time
        self.root.after(0, self._handle_file_changed)

    def _handle_file_changed(self):
        self.status_var.set("Auth file changed. Fetching new quota...")
        self.process_auth_file()

    def manual_fetch(self):
        self.status_var.set("Manual fetch initiated...")
        self.process_auth_file()

    def process_auth_file(self):
        if not os.path.exists(AUTH_FILE_PATH):
            self.clear_session_credentials()
            self.status_var.set("auth.json was removed. Logged out from Codex.")
            return

        try:
            with open(AUTH_FILE_PATH, 'r') as f:
                auth_data = json.load(f)

            jwt = auth_data.get("tokens", {}).get("access_token")
            if jwt:
                # Trigger async fetch to prevent UI freeze
                threading.Thread(target=self._bg_fetch_single, args=(None, jwt), daemon=True).start()
            else:
                self.clear_session_credentials()
                self.status_var.set("No access_token found in auth.json. Logged out from Codex.")
        except json.JSONDecodeError:
            self.status_var.set("auth.json is empty or invalid JSON. Retrying after the next file change.")
        except Exception as e:
            self.status_var.set(f"Failed to parse auth.json: {e}")

    def fetch_api(self, jwt):
        req = urllib.request.Request(USAGE_API_URL)
        req.add_header('Authorization', f'Bearer {jwt}')
        req.add_header('User-Agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36')

        ctx = ssl.create_default_context(cafile=certifi.where())

        # Added timeout=15 to prevent the socket from hanging indefinitely
        with urllib.request.urlopen(req, context=ctx, timeout=15) as response:
            return json.loads(response.read().decode('utf-8'))

    def update_map_from_res(self, res_data, jwt):
        email = res_data.get("email")
        rate_limit = res_data.get("rate_limit", {})
        primary_window = rate_limit.get("primary_window") if rate_limit else {}

        reset_at = primary_window.get("reset_at") if primary_window else None
        used_percent = primary_window.get("used_percent", 0) if primary_window else 0

        if email and reset_at:
            if email not in self.usage_map:
                self.usage_map[email] = {}

            # Keep the token in memory only for this app session.
            self.usage_map[email].update({
                "reset_ts": reset_at,
                "used_percent": used_percent,
                "last_fetched": time.time()
            })
            if "auto_fetch" not in self.usage_map[email]:
                self.usage_map[email]["auto_fetch"] = "None"

            self.set_current_account(email, jwt)
            self.save_data()
            return True
        return False

    def _bg_fetch_single(self, expected_email, jwt):
        if not jwt: return
        try:
            res = self.fetch_api(jwt)
            success = self.update_map_from_res(res, jwt)
            if success:
                email = res.get("email")
                self.root.after(0, self.refresh_ui)
                msg = f"Successfully updated quota for {email}"
                self.root.after(0, lambda m=msg: self.status_var.set(m))
            else:
                msg = "Warning: Could not find email or reset_at in API response"
                self.root.after(0, lambda m=msg: self.status_var.set(m))
                print(f"[Safe Error Log] {msg}")

        except urllib.error.HTTPError as e:
            err_msg = f"HTTP Error {e.code} - Token might be expired"
            print(f"[Safe Error Log] {err_msg}")
            self.root.after(0, lambda m=err_msg: self.status_var.set(m))

        except (urllib.error.URLError, TimeoutError) as e:
            # Properly stringify the error to avoid Tkinter lambda garbage collection crashes
            err_msg = f"Network Error: {getattr(e, 'reason', str(e))}"

            # Diagnose the exact macOS SSL issue and offer a secure solution on the UI
            if "CERTIFICATE_VERIFY_FAILED" in str(getattr(e, 'reason', '')):
                err_msg = "SSL Error: Run 'Install Certificates.command' in Mac Python folder, or run 'pip install certifi'"

            print(f"[Safe Error Log] {err_msg}")

            # Using `m=err_msg` safely locks the string value into the lambda
            self.root.after(0, lambda m=err_msg: self.status_var.set(m))

        except Exception as e:
            err_msg = f"Unknown error: {str(e)}"
            print(f"[Safe Error Log] Exception triggered: {err_msg}")
            self.root.after(0, lambda m=err_msg: self.status_var.set(m))

    def check_auto_fetch(self):
        now = time.time()
        interval_map = {"1 Hr": 3600, "3 Hrs": 10800, "12 Hrs": 43200, "24 Hrs": 86400}

        current_email = self.current_account_email
        if not current_email:
            return

        data = self.usage_map.get(current_email)
        if not data:
            return

        interval_str = data.get("auto_fetch", "None")
        if interval_str == "None":
            return

        interval_sec = interval_map.get(interval_str, 0)
        if interval_sec > 0 and (now - data.get("last_fetched", 0)) >= interval_sec:
            # Update timestamp now to prevent multiple identical threads spawning
            data["last_fetched"] = now
            jwt = self.session_tokens.get(current_email)
            if jwt:
                threading.Thread(target=self._bg_fetch_single, args=(current_email, jwt), daemon=True).start()

    # --- UI RENDERING LOGIC ---
    def refresh_ui(self):
        if self._timer_id:
            self.root.after_cancel(self._timer_id)

        # Triggers our async background check for scheduled table rows
        self.check_auto_fetch()

        for item in self.tree.get_children():
            self.tree.delete(item)

        now_ts = datetime.now().timestamp()

        # Sort by reset timestamp ascending
        sorted_usage = sorted(self.usage_map.items(), key=lambda x: x[1].get("reset_ts", 0))

        for email, data in sorted_usage:
            try:
                reset_ts = data.get("reset_ts", 0)
                used_percent = data.get("used_percent", 0)
                auto_fetch = self.get_display_auto_fetch(email, data)
                is_current = email == self.current_account_email
                email_display = f"{email}   [CURRENT]" if is_current else email

                reset_time_str = datetime.fromtimestamp(reset_ts).strftime('%Y-%m-%d %H:%M')
                quota_left_str = f"{100 - used_percent}%"

                # Check if Ready
                if now_ts >= reset_ts:
                    display_text = f"{reset_time_str} (ready)"
                    countdown_text = "0m"
                else:
                    display_text = reset_time_str

                    diff = int(reset_ts - now_ts)
                    days = diff // 86400
                    hours = (diff % 86400) // 3600
                    minutes = (diff % 3600) // 60

                    time_parts = []
                    if days > 0: time_parts.append(f"{days}d")
                    if hours > 0: time_parts.append(f"{hours}h")
                    time_parts.append(f"{minutes}m")

                    countdown_text = " ".join(time_parts)

                self.tree.insert(
                    '',
                    tk.END,
                    iid=email,
                    values=(email_display, quota_left_str, display_text, countdown_text, auto_fetch),
                    tags=("current_account",) if is_current else ()
                )
            except Exception:
                self.tree.insert('', tk.END, iid=email, values=(email, "Error", "Error", "Error", "-"))

        self.root.after_idle(self.refresh_auto_fetch_editor)
        self._timer_id = self.root.after(60000, self.refresh_ui)

    def on_closing(self):
        if self._timer_id:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None

        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=2)
            self.observer = None

        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = CodexMonitorApp(root)
    root.lift()
    root.attributes('-topmost', True)
    root.after_idle(root.attributes, '-topmost', False)
    root.mainloop()

"""
================================================================================
HOW TO BUILD A NATIVE MACOS APP (.app) FROM THIS SCRIPT
================================================================================
To turn this script into a double-clickable macOS application, the most robust
and native-feeling way is to use "PyInstaller".

STEPS:

1. Open Terminal.app
2. Install PyInstaller using pip:
   pip install pyinstaller

3. Navigate to the directory containing this script (e.g., codex_monitor.py):
   cd /path/to/your/script/folder

4. Run the following build command:
   pyinstaller --windowed --onefile --noconfirm --name "CodexMonitor" codex_monitor.py

5. Once it finishes, look inside the newly created "dist" folder.
   You will find a native "CodexMonitor.app" bundle.

6. Move "CodexMonitor.app" into your /Applications folder, and you can now 
   launch it from Spotlight, Launchpad, or double-clicking just like any Mac App!
================================================================================
"""
