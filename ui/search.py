"""A compact search strip; searching never edits either document pane."""

import re
import tkinter as tk
from tkinter import ttk


class SearchBar(ttk.Frame):
    def __init__(self, parent, editor, on_close):
        super().__init__(parent, padding=(24, 8))
        self.editor = editor
        self.on_close = on_close
        self.query = tk.StringVar()
        self.count = tk.StringVar(value="在中文和英文中查找")
        self.matches = []
        self.current = -1
        self._signature = None
        self._active = False
        ttk.Label(self, text="查找").pack(side="left", padx=(0, 10))
        self.entry = ttk.Entry(self, textvariable=self.query, width=26)
        self.entry.pack(side="left")
        ttk.Label(self, textvariable=self.count).pack(side="left", padx=14)
        ttk.Button(self, text="上一处", command=lambda: self.next(-1)).pack(side="right", padx=4)
        ttk.Button(self, text="下一处", command=self.next).pack(side="right", padx=4)
        ttk.Button(self, text="关闭", command=self.hide).pack(side="right", padx=4)
        self.entry.bind("<Return>", lambda event: self._navigate(1))
        self.entry.bind("<Shift-Return>", lambda event: self._navigate(-1))
        self.entry.bind("<Escape>", lambda event: self._hide_shortcut())
        self.query.trace_add("write", lambda *_: self.refresh())
        for widget in (editor.source, editor.target):
            widget.tag_configure("search_match", background="#fff2c9")
            widget.tag_configure("search_current", background="#ffd986")

    def activate(self):
        self._active = True
        self._signature = None
        self.refresh()
        self.entry.focus_set()
        self.entry.selection_range(0, "end")

    def refresh(self):
        if not self._active:
            return
        query = self.query.get()
        texts = [widget.get("1.0", "end-1c") for widget in (self.editor.source, self.editor.target)]
        signature = (query, *texts)
        if signature == self._signature:
            return
        self._signature = signature
        self.matches = []
        self.current = -1
        for widget, text in zip((self.editor.source, self.editor.target), texts):
            widget.tag_remove("search_match", "1.0", "end")
            widget.tag_remove("search_current", "1.0", "end")
            if query:
                for match in re.finditer(re.escape(query), text, flags=re.IGNORECASE):
                    start, end = self.editor._index(text, match.start()), self.editor._index(text, match.end())
                    self.matches.append((widget, start, end))
                    widget.tag_add("search_match", start, end)
            widget.tag_raise("search_match")
            widget.tag_raise("search_current")
        self.count.set(f"共 {len(self.matches)} 处" if query else "在中文和英文中查找")

    def next(self, direction=1):
        self.refresh()
        if not self.matches:
            return
        self.current = ((len(self.matches) - 1 if direction < 0 else 0) if self.current == -1
                        else (self.current + direction) % len(self.matches))
        for widget in (self.editor.source, self.editor.target):
            widget.tag_remove("search_current", "1.0", "end")
        widget, start, end = self.matches[self.current]
        widget.tag_add("search_current", start, end)
        widget.tag_raise("search_current")
        self.editor._take_scroll(widget)
        widget.see(start)
        self.count.set(f"{self.current + 1} / {len(self.matches)}")

    def _navigate(self, direction):
        self.next(direction)
        return "break"

    def _hide_shortcut(self):
        self.hide()
        return "break"

    def hide(self):
        self._active = False
        for widget in (self.editor.source, self.editor.target):
            widget.tag_remove("search_match", "1.0", "end")
            widget.tag_remove("search_current", "1.0", "end")
        self.on_close()
        self.editor.focus_source()
