"""Browse automatic recovery copies without exposing internal filenames."""

from datetime import datetime
import tkinter as tk
from tkinter import ttk

from backend.storage import load_document


class RecoveryDialog(tk.Toplevel):
    def __init__(self, parent, directory, on_restore):
        super().__init__(parent)
        self.title("恢复文档备份")
        self.geometry("720x460")
        self.minsize(540, 340)
        self.transient(parent)
        self.on_restore = on_restore
        self.paths = {}
        self.documents = {}
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="新建或打开其他文档前，会自动备份当前内容。恢复后可另存为文件。").pack(anchor="w", pady=(0, 12))
        table_frame = ttk.Frame(body)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=("title", "time"), show="headings", selectmode="browse", height=7)
        self.table.heading("title", text="文档")
        self.table.heading("time", text="备份时间")
        self.table.column("title", width=400)
        self.table.column("time", width=170, stretch=False)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.preview = tk.Text(body, height=6, wrap="word", relief="flat", background="#f4f7fc",
                               font=("Microsoft YaHei UI", 10), padx=10, pady=8, state="disabled")
        self.preview.pack(fill="x", pady=12)
        actions = ttk.Frame(body)
        actions.pack(fill="x")
        self.notice = ttk.Label(actions, text="暂无备份。切换文档后，备份会显示在这里。")
        self.notice.pack(side="left")
        self.restore_button = ttk.Button(actions, text="恢复所选文档", command=self.restore, state="disabled")
        self.restore_button.pack(side="right")
        self.table.bind("<<TreeviewSelect>>", self._select)
        self.table.bind("<Double-1>", lambda event: self.restore())
        self.bind("<Escape>", lambda event: self.destroy())
        skipped = 0
        try:
            paths = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
            for path in paths[:100]:
                try:
                    document = load_document(path)
                    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                except (OSError, ValueError, TypeError, KeyError):
                    skipped += 1
                    continue
                row = self.table.insert("", "end", values=(document.title, stamp))
                self.paths[row] = path
                self.documents[row] = document
        except OSError:
            self.notice.configure(text="无法读取备份目录，请检查文件权限。")
        if self.paths:
            self.notice.configure(text=f"显示最近 {len(self.paths)} 份备份" + (f" · {skipped} 份无法读取" if skipped else ""))
            self.table.selection_set(next(iter(self.paths)))
        elif skipped:
            self.notice.configure(text=f"{skipped} 份备份无法读取，文件已保留。")
        self.grab_set()

    def _select(self, _event=None):
        selected = self.table.selection()
        if not selected:
            return
        document = self.documents[selected[0]]
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", document.source[:2000])
        self.preview.configure(state="disabled")
        self.restore_button.configure(state="normal")

    def restore(self):
        selected = self.table.selection()
        if selected:
            if self.on_restore(self.paths[selected[0]]):
                self.destroy()
