import tkinter as tk

from .ui import CodexMonitorApp


def main() -> None:
    root = tk.Tk()
    CodexMonitorApp(root)
    root.lift()
    root.attributes("-topmost", True)
    root.after_idle(root.attributes, "-topmost", False)
    root.mainloop()
