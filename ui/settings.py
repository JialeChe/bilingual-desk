import tkinter as tk
import os
from tkinter import filedialog, messagebox, ttk


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, settings: dict, on_save, api_key: str = ""):
        super().__init__(parent)
        self.title("翻译设置")
        self.resizable(False, False)
        self.transient(parent)
        self.on_save = on_save
        self.providers = {"DeepSeek API · Flash": "deepseek", "本地 Codex · Luna": "codex"}
        label = next((label for label, value in self.providers.items()
                      if value == settings.get("provider", "deepseek")), "DeepSeek API · Flash")
        self.provider = tk.StringVar(value=label)
        self.api_key = tk.StringVar(value=api_key)
        self.path = tk.StringVar(value=settings.get("codex_path", ""))
        self.timeout = tk.IntVar(value=settings.get("timeout", 90))
        self.fast_updates = tk.BooleanVar(value=settings.get("fast_updates", False))
        self.auto = tk.BooleanVar(value=settings.get("auto", True))
        self.organize_imports = tk.BooleanVar(value=settings.get("organize_imports", False))
        body = ttk.Frame(self, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="翻译引擎").grid(row=0, column=0, sticky="w", padx=(0, 10))
        picker = ttk.Combobox(body, values=tuple(self.providers), textvariable=self.provider,
                              state="readonly", width=29)
        picker.grid(row=0, column=1, sticky="w")
        picker.bind("<<ComboboxSelected>>", self._select_provider)
        self.codex_frame = ttk.Frame(body)
        self.codex_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        ttk.Label(self.codex_frame, text="使用 Codex 的 ChatGPT 订阅登录，无需 API Key。",
                  foreground="#66717e").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))
        ttk.Label(self.codex_frame, text="Codex 程序").grid(row=1, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(self.codex_frame, textvariable=self.path, width=46).grid(row=1, column=1)
        ttk.Button(self.codex_frame, text="选择…", command=self._browse).grid(row=1, column=2, padx=(8, 0))
        ttk.Checkbutton(self.codex_frame, text="后续局部更新使用 Fast 模式", variable=self.fast_updates).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(14, 0))
        ttk.Label(self.codex_frame, text="首次翻译仍用标准速度。Fast 通常消耗 2.5 倍订阅额度。",
                  foreground="#66717e", wraplength=490).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(self.codex_frame, text="未登录时，请先在终端运行 codex login。",
                  foreground="#66717e").grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.deepseek_frame = ttk.Frame(body)
        self.deepseek_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        ttk.Label(self.deepseek_frame, text="DeepSeek Flash · 已关闭思考 · 使用 DeepSeek API 余额",
                  foreground="#66717e").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        ttk.Label(self.deepseek_frame, text="API Key").grid(row=1, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(self.deepseek_frame, textvariable=self.api_key, show="•", width=49).grid(row=1, column=1)
        ttk.Label(self.deepseek_frame, text="Windows 会加密保存密钥；留空时使用 DEEPSEEK_API_KEY 环境变量。",
                  foreground="#66717e", wraplength=490).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(body, text="每批等待时间").grid(row=3, column=0, sticky="w", pady=14)
        row = ttk.Frame(body)
        row.grid(row=3, column=1, sticky="w")
        ttk.Spinbox(row, from_=30, to=300, textvariable=self.timeout, width=6).pack(side="left")
        ttk.Label(row, text=" 秒").pack(side="left")
        ttk.Checkbutton(body, text="自动更新", variable=self.auto).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Checkbutton(body, text="导入时自动整理段落（可撤销）", variable=self.organize_imports).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Button(body, text="保存", command=self._save).grid(row=6, column=2, pady=(8, 0))
        self._select_provider()
        self.grab_set()
        self.wait_visibility()
        self.focus_set()

    def _select_provider(self, _event=None):
        if self.providers[self.provider.get()] == "deepseek":
            self.codex_frame.grid_remove()
            self.deepseek_frame.grid()
        else:
            self.deepseek_frame.grid_remove()
            self.codex_frame.grid()

    def _browse(self):
        path = filedialog.askopenfilename(parent=self, title="选择 Codex 程序",
                                         filetypes=[("Codex", "codex.exe"), ("所有文件", "*.*")])
        if path:
            self.path.set(path)

    def _save(self):
        try:
            timeout = int(self.timeout.get())
            if not 30 <= timeout <= 300:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror("设置", "等待时间应为 30–300 秒。", parent=self)
            return
        provider = self.providers[self.provider.get()]
        key = self.api_key.get().strip()
        if provider == "deepseek" and not (key or os.environ.get("DEEPSEEK_API_KEY", "").strip()):
            messagebox.showerror("设置", "请填写 DeepSeek API Key。", parent=self)
            return
        if "\n" in key or "\r" in key:
            messagebox.showerror("设置", "API Key 不能包含换行。", parent=self)
            return
        saved = self.on_save({"provider": provider, "codex_path": self.path.get().strip(), "timeout": timeout,
                              "fast_updates": self.fast_updates.get(), "auto": self.auto.get(),
                              "organize_imports": self.organize_imports.get(),
                              "deepseek_api_key": key})
        if saved is False:
            return
        self.destroy()
