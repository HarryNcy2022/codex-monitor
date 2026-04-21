import customtkinter as ctk

from .ui import CodexMonitorApp


def main() -> None:
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("green")

    root = ctk.CTk()
    CodexMonitorApp(root)
    root.lift()
    root.attributes("-topmost", True)
    root.after_idle(root.attributes, "-topmost", False)
    root.mainloop()
