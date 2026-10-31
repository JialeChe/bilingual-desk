"""Small native widget theme and vector toolbar icons."""

import tkinter as tk
from tkinter import ttk

WHITE = "#ffffff"
INK = "#20242d"
MUTED = "#858b98"
LINE = "#e8ebf0"
BLUE = "#2f6edb"


def configure_style(root):
    style = ttk.Style(root)
    if style.theme_use() != "clam":
        style.theme_use("clam")
    style.configure("TFrame", background=WHITE)
    style.configure("TLabel", background=WHITE, foreground=INK, font=("Microsoft YaHei UI", 10))
    style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 7),
                    background=WHITE, foreground=INK, bordercolor=LINE, lightcolor=WHITE,
                    darkcolor=WHITE, focuscolor=WHITE)
    style.map("TButton", background=[("active", "#f4f7fc")],
              foreground=[("disabled", "#b2b7c1")])
    style.configure("Quiet.TMenubutton", background=WHITE, foreground=INK,
                    font=("Microsoft YaHei UI", 11), padding=(8, 8), borderwidth=0,
                    arrowsize=0, relief="flat")
    style.map("Quiet.TMenubutton", background=[("active", "#f4f7fc")],
              foreground=[("disabled", "#b2b7c1")])
    style.configure("Open.TButton", foreground=BLUE, bordercolor="#82a9ec", padding=(14, 8))
    style.map("Open.TButton", bordercolor=[("active", BLUE)])
    style.configure("Translate.TButton", background=BLUE, foreground=WHITE,
                    bordercolor=BLUE, lightcolor=BLUE, darkcolor=BLUE, padding=(14, 8))
    style.map("Translate.TButton", background=[("disabled", "#edf1f7"), ("active", "#265fc2")],
              foreground=[("disabled", "#9aa6b7"), ("active", WHITE)],
              bordercolor=[("disabled", "#edf1f7"), ("active", "#265fc2")])
    style.configure("Pane.TFrame", background=WHITE)
    style.configure("PaneHeading.TLabel", background=WHITE, foreground=MUTED,
                    font=("Microsoft YaHei UI", 11))
    style.configure("Reader.Vertical.TScrollbar", background="#cdd2dc", troughcolor=WHITE,
                    bordercolor=WHITE, lightcolor=WHITE, darkcolor=WHITE,
                    arrowsize=10, gripcount=0, borderwidth=0)
    style.layout("Reader.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
        "sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"sticky": "nswe"})]})])
    style.map("Reader.Vertical.TScrollbar", background=[("active", "#adb6c6")])
    style.configure("Footer.TLabel", font=("Microsoft YaHei UI", 9), foreground=MUTED)
    style.configure("TCheckbutton", font=("Microsoft YaHei UI", 10), background=WHITE,
                    foreground=INK, focuscolor=WHITE, padding=(4, 5))
    style.map("TCheckbutton", background=[("active", WHITE)],
              indicatorbackground=[("selected", BLUE), ("!selected", WHITE)])
    style.configure("Reader.Horizontal.TProgressbar", background=BLUE, troughcolor="#edf1f7",
                    bordercolor=WHITE, lightcolor=BLUE, darkcolor=BLUE, thickness=4)
    # Deliver the native theme event while its root is alive, including short-lived windows.
    root.update_idletasks()
    return style


class IconButton(tk.Canvas):
    def __init__(self, parent, icon, command):
        super().__init__(parent, width=36, height=36, background=WHITE,
                         highlightthickness=0, takefocus=True, cursor="hand2")
        self.command = command
        if icon == "search":
            self.create_oval(9, 7, 23, 21, outline=INK, width=1.8)
            self.create_line(21, 19, 28, 27, fill=INK, width=1.8)
        else:
            self.create_text(18, 18, text="Aa", font=("Segoe UI", 14), fill=INK)
        self.bind("<Enter>", lambda event: self.configure(background="#f4f7fc"))
        self.bind("<Leave>", lambda event: self.configure(background=WHITE))
        self.bind("<FocusIn>", lambda event: self.configure(background="#eef4ff"))
        self.bind("<FocusOut>", lambda event: self.configure(background=WHITE))
        for event in ("<Button-1>", "<Return>", "<space>"):
            self.bind(event, lambda event: self.invoke())

    def invoke(self):
        self.command()
        return "break"
