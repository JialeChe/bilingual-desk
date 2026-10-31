"""Nonmodal, live diagnostics viewer using only native Tk widgets."""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path


class LogWindow(tk.Toplevel):
    def __init__(self, parent, diagnostics):
        super().__init__(parent)
        self.diagnostics = diagnostics
        self._timer = None
        self._last_sequences = None
        self._last_state = None
        self.title("排查日志 · Ctrl+Shift+L")
        self.geometry("920x620")
        self.minsize(640, 400)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda event: self.close())
        self.bind("<Control-Shift-L>", lambda event: self._focus())
        self.bind("<Control-Shift-l>", lambda event: self._focus())
        toolbar = ttk.Frame(self, padding=(12, 10))
        toolbar.pack(fill="x")
        self.errors_only = tk.BooleanVar(value=False)
        self.follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="只看错误", variable=self.errors_only,
                        command=self._refresh).pack(side="left")
        ttk.Checkbutton(toolbar, text="跟随最新", variable=self.follow).pack(side="left", padx=12)
        ttk.Button(toolbar, text="导出日志", command=self.export).pack(side="right")
        ttk.Button(toolbar, text="复制全部", command=self.copy_all).pack(side="right", padx=8)
        pane = ttk.Frame(self, padding=(12, 0))
        pane.pack(fill="both", expand=True)
        self.text = tk.Text(pane, wrap="word", state="disabled", relief="flat", padx=10, pady=10,
                            font=("Consolas", 10), background="white", foreground="#243040")
        scroll = ttk.Scrollbar(pane, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("ERROR", foreground="#b42318")
        self.text.tag_configure("WARNING", foreground="#986800")
        self.text.bind("<Control-a>", self._select_all)
        self.notice = tk.StringVar()
        ttk.Label(self, textvariable=self.notice, wraplength=880,
                  padding=(12, 10)).pack(fill="x")
        self._refresh()
        self._timer = self.after(400, self._tick)

    def _focus(self):
        self.lift()
        self.focus_set()
        return "break"

    def _select_all(self, event):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _refresh(self):
        state = (self.diagnostics.version, self.errors_only.get(), self.diagnostics.write_error)
        if state == self._last_state:
            return
        self._last_state = state
        entries = self.diagnostics.snapshot()
        shown = [e for e in entries if not self.errors_only.get() or e["level"] == "ERROR"]
        sequences = tuple(e["sequence"] for e in shown)
        if sequences != self._last_sequences:
            position = self.text.yview()[0]
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            for entry in shown:
                self.text.insert("end", self.diagnostics.format([entry]) + "\n\n", entry["level"])
            if not shown:
                self.text.insert("end", "暂无错误日志。" if self.errors_only.get() else "暂无日志，操作应用后会自动记录。")
            self.text.configure(state="disabled")
            if self.follow.get():
                self.text.see("end")
            else:
                self.text.yview_moveto(position)
            self._last_sequences = sequences
        if self.diagnostics.write_error:
            self.notice.set("日志文件写入失败，仍可查看、复制或导出当前日志：" + self.diagnostics.write_error)
        else:
            self.notice.set(f"本次运行 {len(entries)} 条 · 日志含文档片段，分享前请检查。选中文字可 Ctrl+C。")

    def _tick(self):
        self._timer = None
        self._refresh()
        self._timer = self.after(400, self._tick)

    def copy_all(self):
        self.clipboard_clear()
        self.clipboard_append(self.diagnostics.format())

    def export(self):
        path = filedialog.asksaveasfilename(parent=self, title="导出排查日志",
                    initialfile="翻译排查日志.txt", defaultextension=".txt", filetypes=[("文本日志", "*.txt")])
        if path:
            try:
                self.diagnostics.export(Path(path))
                self.notice.set("日志已导出。")
            except OSError as error:
                messagebox.showerror("导出失败", str(error), parent=self)

    def close(self):
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
        self.destroy()
