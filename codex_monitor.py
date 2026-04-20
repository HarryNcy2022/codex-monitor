import os
import json
import time
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk
from datetime import datetime
import threading
import select
import ssl
import certifi

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


class CodexMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Codex Account Monitor")
        self.root.geometry("850x350")
        self.root.minsize(700, 250)

        self.usage_map = self.load_data()
        self.last_mtime = 0
        if os.path.exists(AUTH_FILE_PATH):
            self.last_mtime = os.path.getmtime(AUTH_FILE_PATH)

        self._timer_id = None

        self.setup_ui()
        self.refresh_ui()

        # Start the macOS native kernel watcher in the background
        self.start_kqueue_watcher()

    def setup_ui(self):
        # Top frame for buttons
        top_frame = tk.Frame(self.root)
        top_frame.pack(fill=tk.X, padx=10, pady=5)

        btn_manual = ttk.Button(top_frame, text="Manual Fetch (Current auth.json)", command=self.manual_fetch)
        btn_manual.pack(side=tk.LEFT)

        # Main Table frame
        frame = tk.Frame(self.root)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ('Email', 'Quota Left', 'Reset Time', 'Time Until Reset', 'Auto-Fetch')
        self.tree = ttk.Treeview(frame, columns=columns, show='headings')
        self.tree.heading('Email', text='Account Email')
        self.tree.heading('Quota Left', text='Quota Left')
        self.tree.heading('Reset Time', text='Reset Time')
        self.tree.heading('Time Until Reset', text='Time Until Reset')
        self.tree.heading('Auto-Fetch', text='Auto-Fetch ▾')

        self.tree.column('Email', width=220)
        self.tree.column('Quota Left', width=100, anchor=tk.CENTER)
        self.tree.column('Reset Time', width=180, anchor=tk.CENTER)
        self.tree.column('Time Until Reset', width=130, anchor=tk.CENTER)
        self.tree.column('Auto-Fetch', width=100, anchor=tk.CENTER)

        self.tree.pack(fill=tk.BOTH, expand=True)

        # Bind click for dropdown injection
        self.tree.bind("<ButtonRelease-1>", self.on_tree_click)

        # Status Bar
        self.status_var = tk.StringVar()
        self.status_var.set(f"Watching: {AUTH_FILE_PATH} (kqueue)")

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
        status_label.pack(side=tk.BOTTOM, fill=tk.X, pady=5, padx=10)

    def load_data(self):
        data = {}
        if os.path.exists(LOCAL_STORAGE_FILE):
            try:
                with open(LOCAL_STORAGE_FILE, 'r') as f:
                    raw = json.load(f)
                    # Migrate old schema {email: reset_ts} to new dict schema
                    for k, v in raw.items():
                        if isinstance(v, (int, float)):
                            data[k] = {
                                "reset_ts": v, "used_percent": 0, "jwt": None,
                                "auto_fetch": "None", "last_fetched": 0
                            }
                        else:
                            data[k] = v
            except Exception:
                pass
        return data

    def save_data(self):
        try:
            with open(LOCAL_STORAGE_FILE, 'w') as f:
                json.dump(self.usage_map, f)
        except Exception as e:
            print(f"Failed to save data: {e}")

    # --- TREEVIEW COMBOBOX (DROPDOWN) LOGIC ---
    def on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell": return

        column = self.tree.identify_column(event.x)
        # Column #5 is 'Auto-Fetch'
        if column == "#5":
            item = self.tree.identify_row(event.y)
            if not item: return

            email = self.tree.item(item, "values")[0]
            current_val = self.usage_map.get(email, {}).get("auto_fetch", "None")

            x, y, w, h = self.tree.bbox(item, column)

            # Create combobox over the cell
            cb = ttk.Combobox(self.tree, values=["None", "1 Hr", "3 Hrs", "12 Hrs", "24 Hrs"], state="readonly")
            cb.place(x=x, y=y, width=w, height=h)
            cb.set(current_val)

            def save_val(e=None):
                new_val = cb.get()
                if email in self.usage_map:
                    self.usage_map[email]["auto_fetch"] = new_val
                    self.save_data()
                    self.refresh_ui()
                cb.destroy()

            cb.bind("<<ComboboxSelected>>", save_val)
            cb.bind("<FocusOut>", lambda e: cb.destroy())
            cb.focus_set()

    # --- BACKGROUND WATCHER & FETCH LOGIC ---
    def start_kqueue_watcher(self):
        os.makedirs(AUTH_DIR, exist_ok=True)
        watcher_thread = threading.Thread(target=self._kqueue_watch_loop, daemon=True)
        watcher_thread.start()

    def _kqueue_watch_loop(self):
        fd = os.open(AUTH_DIR, os.O_RDONLY)
        kq = select.kqueue()

        flags = select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_RENAME
        ev = select.kevent(fd, filter=select.KQ_FILTER_VNODE,
                           flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                           fflags=flags)

        while True:
            events = kq.control([ev], 1, None)
            if events:
                try:
                    if os.path.exists(AUTH_FILE_PATH):
                        current_mtime = os.path.getmtime(AUTH_FILE_PATH)
                        if current_mtime > self.last_mtime:
                            self.last_mtime = current_mtime
                            self.root.after(0, self.on_file_changed)
                except Exception as e:
                    print(f"Error checking file: {e}")

    def on_file_changed(self):
        self.status_var.set("Auth file changed. Fetching new quota...")
        self.root.update_idletasks()
        self.process_auth_file()

    def manual_fetch(self):
        self.status_var.set("Manual fetch initiated...")
        self.process_auth_file()

    def process_auth_file(self):
        try:
            with open(AUTH_FILE_PATH, 'r') as f:
                auth_data = json.load(f)

            jwt = auth_data.get("tokens", {}).get("access_token")
            if jwt:
                # Trigger async fetch to prevent UI freeze
                threading.Thread(target=self._bg_fetch_single, args=(None, jwt), daemon=True).start()
            else:
                self.status_var.set("No access_token found in auth.json")
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

            # Store the token inside map to allow scheduled fetching without relying on auth.json
            self.usage_map[email].update({
                "reset_ts": reset_at,
                "used_percent": used_percent,
                "jwt": jwt,
                "last_fetched": time.time()
            })
            if "auto_fetch" not in self.usage_map[email]:
                self.usage_map[email]["auto_fetch"] = "None"

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

        for email, data in self.usage_map.items():
            interval_str = data.get("auto_fetch", "None")
            if interval_str == "None":
                continue

            interval_sec = interval_map.get(interval_str, 0)
            if interval_sec > 0 and (now - data.get("last_fetched", 0)) >= interval_sec:
                # Update timestamp now to prevent multiple identical threads spawning
                data["last_fetched"] = now
                jwt = data.get("jwt")
                if jwt:
                    threading.Thread(target=self._bg_fetch_single, args=(email, jwt), daemon=True).start()

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
                auto_fetch = data.get("auto_fetch", "None")

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

                self.tree.insert('', tk.END, values=(email, quota_left_str, display_text, countdown_text, auto_fetch))
            except Exception:
                self.tree.insert('', tk.END, values=(email, "Error", "Error", "Error", "None"))

        self._timer_id = self.root.after(60000, self.refresh_ui)


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
