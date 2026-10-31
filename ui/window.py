"""Desktop orchestration. Widgets are only touched from the Tk main thread."""

import json
from pathlib import Path
import queue
import re
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from backend.jobs import JobRunner
from backend.diagnostics import Diagnostics
from backend.credentials import CredentialError, load_deepseek_key, save_deepseek_key
from backend.models import Document
from backend.providers.codex import CodexEngine, SessionBusyError
from backend.providers.deepseek import DeepSeekEngine
from backend.providers.rpc import discover_codex
from backend.storage import load_document, save_document, write_json
from backend.bilingual import apply_response, prepare_request, resolve_conflicts, ConflictingEdits
from .editor import BilingualEditor
from .settings import SettingsDialog
from .logs import LogWindow
from .appearance import BLUE, INK, LINE, WHITE, IconButton, configure_style
from .search import SearchBar
from .history import RecoveryDialog


class MainWindow:
    def __init__(self, root: tk.Tk, data_dir: Path, auto_connect: bool = True):
        self.root = root
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.diagnostics = Diagnostics(self.data_dir / "debug.jsonl")
        self._log_window = None
        self.document = Document()
        self.file_path = None
        self._saved_snapshot = None
        self._operation_kind = None
        self._operation_started = 0
        self._operation_message = ""
        self._cancel_requested = False
        self._cancelled_revision = None
        self.busy = False
        self._closed = False
        self._auto_timer = None
        self._save_timer = None
        self._poll_timer = None
        self._startup_timer = None
        self._failed_revision = None
        self.settings = self._load_settings()
        self.deepseek_key = ""
        self._key_error = None
        try:
            self.deepseek_key = load_deepseek_key(self.data_dir / "deepseek_key.bin")
        except CredentialError as error:
            self._key_error = str(error)
        self.diagnostics.register_secret(self.deepseek_key)
        self.engine = self._new_engine()
        self.jobs = JobRunner(self.diagnostics)
        self.title = tk.StringVar(value=self.document.title)
        self.status = tk.StringVar(value="左侧中文，右侧英文；修改任意一侧可更新另一侧。")
        self.connection = tk.StringVar(value=self._engine_status())
        self.auto = tk.BooleanVar(value=self.settings.get("auto", True))
        self.sync_state = tk.StringVar(value="等待输入")
        self.save_state = tk.StringVar(value="草稿自动保存")
        self._build()
        self._load_draft()
        self._refresh_ui()
        if self._key_error and self.settings["provider"] == "deepseek":
            self.status.set(self._key_error)
        self.title.trace_add("write", self._title_changed)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = self._callback_error
        self.root.bind("<Control-Shift-L>", lambda event: self._shortcut(self.show_logs))
        self.root.bind("<Control-Shift-l>", lambda event: self._shortcut(self.show_logs))
        self.diagnostics.record("应用启动", provider=self.engine.name)
        for shortcut, action in (("<Control-s>", self.save), ("<Control-o>", self.open),
                                 ("<Control-n>", self.new), ("<Control-Return>", self.translate),
                                 ("<Control-f>", self.show_search)):
            self.root.bind(shortcut, lambda event, callback=action: self._shortcut(callback))
        self._poll_timer = self.root.after(100, self._poll)
        if auto_connect:
            self._startup_timer = self.root.after(250, self._activate_engine)

    def _new_engine(self):
        if self.settings.get("provider") == "deepseek":
            engine = DeepSeekEngine(self.deepseek_key, self.settings.get("timeout", 90),
                                  timings_path=self.data_dir / "translation_timings.jsonl")
        else:
            engine = CodexEngine(self.settings.get("codex_path", ""), self.settings.get("timeout", 90),
                           sessions_path=self.data_dir / "codex_sessions.json",
                           fast_updates=self.settings.get("fast_updates", False),
                           timings_path=self.data_dir / "translation_timings.jsonl")
        engine.set_diagnostics(self.diagnostics)
        return engine

    def _load_settings(self):
        defaults = {"codex_path": discover_codex(), "timeout": 90, "auto": True,
                    "fast_updates": False, "provider": "deepseek", "font_size": 16,
                    "organize_imports": False, "recent_files": []}
        try:
            saved = json.loads((self.data_dir / "settings.json").read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                if saved.get("provider") in ("codex", "deepseek"):
                    defaults["provider"] = saved["provider"]
                path = saved.get("codex_path")
                if isinstance(path, str):
                    defaults["codex_path"] = path
                timeout = saved.get("timeout")
                if type(timeout) is int and 30 <= timeout <= 300:
                    defaults["timeout"] = timeout
                if type(saved.get("auto")) is bool:
                    defaults["auto"] = saved["auto"]
                if type(saved.get("organize_imports")) is bool:
                    defaults["organize_imports"] = saved["organize_imports"]
                recent = saved.get("recent_files")
                if isinstance(recent, list):
                    defaults["recent_files"] = [p for p in recent if isinstance(p, str)][:8]
                if type(saved.get("fast_updates")) is bool:
                    defaults["fast_updates"] = saved["fast_updates"]
                if type(saved.get("font_size")) is int and saved["font_size"] in (14, 16, 18, 20):
                    defaults["font_size"] = saved["font_size"]
        except (OSError, ValueError):
            pass
        return defaults

    def _build(self):
        self.root.title("翻译对照")
        self.root.geometry("1280x820")
        self.root.minsize(800, 480)
        self.root.configure(background=WHITE)
        configure_style(self.root)
        toolbar = ttk.Frame(self.root, padding=(16, 10))
        toolbar.pack(fill="x")
        self.document_menu = tk.Menu(self.root, tearoff=False, font=("Microsoft YaHei UI", 10),
                                     background=WHITE, foreground=INK, activebackground="#eef4ff",
                                     activeforeground=BLUE, borderwidth=1)
        self.document_menu.add_command(label="新建文档", accelerator="Ctrl+N", command=self.new)
        self.document_menu.add_command(label="保存文档", accelerator="Ctrl+S", command=self.save)
        self.document_menu.add_separator()
        self.document_menu.add_command(label="翻译设置", command=self.show_settings)
        self.document_menu.add_command(label="排查日志", accelerator="Ctrl+Shift+L", command=self.show_logs)
        self.document_menu.add_separator()
        self.recent_menu = tk.Menu(self.document_menu, tearoff=False, postcommand=self._populate_recent)
        self.document_menu.add_cascade(label="最近文档", menu=self.recent_menu)
        self.document_menu.add_command(label="恢复备份…", command=self.show_recovery)
        self.document_menu.add_separator()
        self.document_menu.add_command(label="复制中文", command=lambda: self.copy_translation("zh"))
        self.document_menu.add_command(label="导出中文 TXT / Markdown…", command=lambda: self.export_translation("zh"))
        self.document_menu.add_command(label="复制英文", command=self.copy_translation)
        self.document_menu.add_command(label="导出英文 TXT / Markdown…", command=self.export_translation)
        self.settings_button = ttk.Button(toolbar, text="设置", command=self.show_settings)
        self.settings_button.pack(side="left", padx=(0, 8))
        self.document_button = ttk.Menubutton(toolbar, text="☰  文档", style="Quiet.TMenubutton",
                                        menu=self.document_menu)
        self.document_button.pack(side="left")
        tk.Frame(toolbar, background=LINE, width=1, height=24).pack(side="left", padx=(14, 18))
        self.title_entry = tk.Entry(toolbar, textvariable=self.title, width=18, relief="flat",
                                   borderwidth=0, background=WHITE, foreground=INK,
                                   insertbackground=BLUE, font=("Microsoft YaHei UI", 11),
                                   selectbackground="#deebff")
        self.title_entry.pack(side="left", fill="x", expand=True, padx=(0, 20))
        self.font_button = IconButton(toolbar, "font", self.show_font_menu)
        self.font_button.pack(side="right", padx=(12, 0))
        IconButton(toolbar, "search", self.show_search).pack(side="right", padx=(12, 0))
        self.translate_button = ttk.Button(toolbar, text="翻译 / 更新", style="Translate.TButton",
                                           command=self._translation_action)
        self.translate_button.pack(side="right", padx=(10, 0))
        self.open_button = ttk.Button(toolbar, text="打开文件", style="Open.TButton", command=self.open)
        self.open_button.pack(side="right")
        tk.Frame(self.root, background=LINE, height=1).pack(fill="x")
        controls = ttk.Frame(self.root, padding=(28, 6))
        controls.pack(fill="x")
        self.sync_label = ttk.Label(controls, textvariable=self.sync_state, foreground="#66717e")
        self.sync_label.pack(side="left")
        self.progress = ttk.Progressbar(controls, mode="indeterminate", length=70,
                                       style="Reader.Horizontal.TProgressbar")
        ttk.Checkbutton(controls, text="自动翻译", variable=self.auto,
                        command=self._toggle_auto).pack(side="right", padx=(16, 0))
        self.copy_button = ttk.Button(controls, text="复制英文", command=self.copy_translation)
        self.copy_button.pack(side="right")
        self.font_size = tk.IntVar(value=self.settings["font_size"])
        self.font_menu = tk.Menu(self.root, tearoff=False, font=("Microsoft YaHei UI", 10))
        for size, label in ((14, "紧凑"), (16, "标准"), (18, "较大"), (20, "大字")):
            self.font_menu.add_radiobutton(label=f"{label}  ·  {size}", value=size,
                                           variable=self.font_size, command=self._change_font_size)
        footer = ttk.Frame(self.root, padding=(24, 8))
        footer.pack(side="bottom", fill="x")
        tk.Frame(self.root, background=LINE, height=1).pack(side="bottom", fill="x")
        self.editor = BilingualEditor(self.root, self._edited, font_size=self.font_size.get(),
                                      on_target_change=self._edited_target)
        self.conflict_bar = ttk.Frame(self.root, padding=(28, 8))
        ttk.Label(self.conflict_bar, text="同一句两侧都改过，请选择该句以哪边为准：").pack(side="left")
        ttk.Button(self.conflict_bar, text="以中文为准", command=lambda: self._resolve_conflicts("zh")).pack(side="left", padx=6)
        ttk.Button(self.conflict_bar, text="以英文为准", command=lambda: self._resolve_conflicts("en")).pack(side="left")
        self.search_bar = SearchBar(self.root, self.editor, lambda: self.search_bar.pack_forget())
        self.editor.pack(fill="both", expand=True, padx=(4, 8), pady=(0, 8))
        # Recovery is offered only after a session ownership conflict.
        self.reset_session_button = ttk.Button(footer, text="重建会话", command=self.reset_session)
        ttk.Label(footer, textvariable=self.connection, style="Footer.TLabel").pack(side="right", padx=(12, 0))
        self.status_label = ttk.Label(footer, textvariable=self.status, style="Footer.TLabel", wraplength=800)
        self.status_label.pack(side="left", fill="x", expand=True)
        footer.bind("<Configure>", lambda event: self.status_label.configure(wraplength=max(240, event.width - 340)))
        self.save_label = ttk.Label(self.root, textvariable=self.save_state, style="Footer.TLabel",
                                    padding=(28, 0, 28, 4))
        self.save_label.pack(side="bottom", fill="x", before=footer)

    def _toggle_auto(self):
        if self.auto.get():
            self._failed_revision = None
            self._cancelled_revision = None
        self._persist_settings()
        self._schedule_auto()
        self.status.set("自动翻译已开启，停止输入后更新。" if self.auto.get()
                        else "自动翻译已暂停，可点击更新或按 Ctrl+Enter。")

    def _translation_action(self):
        if self.busy:
            self.cancel_translation()
        else:
            self.translate()

    def _refresh_ui(self):
        pending = self.document.pending
        failed = self._failed_revision == self.document.revision
        if self.busy:
            state = "正在更新 · 译文尚未同步" if pending else "正在处理…"
        elif not self.document.source and not self.document.target:
            state = "等待输入"
        elif pending and self._cancelled_revision == self.document.revision:
            state = "已取消更新 · 当前译文尚未同步"
        elif pending and failed:
            state = "更新失败 · 当前译文尚未同步"
        elif pending:
            en_changed = self.document.source != self.document.baseline
            zh_changed = self.document.target != self.document.target_baseline
            state = ("两侧都有修改 · 尚未同步" if en_changed and zh_changed else
                     "中文有修改 · 英文尚未同步" if zh_changed else "英文有修改 · 中文尚未同步")
        else:
            state = "译文已同步"
        self.sync_state.set(state)
        self.sync_label.configure(foreground="#a46814" if pending else "#66717e")
        label = ("正在取消…" if self._cancel_requested else "取消翻译") if self.busy else (
            "重试翻译" if failed and self._cancelled_revision != self.document.revision else
            "更新译文" if self.document.segments else "开始翻译")
        self.translate_button.configure(text=label, state=("disabled" if self.busy and
            (self._operation_kind != "translation" or self._cancel_requested) else "normal"))
        self.copy_button.configure(state="normal" if self.document.source else "disabled")
        if self.busy:
            elapsed = int(time.monotonic() - self._operation_started) if self._operation_started else 0
            message = "正在取消，现有译文已保留…" if self._cancel_requested else self._operation_message
            progress = getattr(self.engine, "progress", None)
            if progress and not self._cancel_requested:
                message += f" · 已完成 {progress[1]}/{progress[2]} 批"
            if message:
                queued = " · 有新修改待更新" if (getattr(self, "_active_revision", None) is not None
                         and self.document.revision != self._active_revision) else ""
                self.status.set(f"{message} · 已等待 {elapsed} 秒{queued}")

    def _begin_operation(self, kind, message):
        if self._auto_timer:
            self.root.after_cancel(self._auto_timer)
            self._auto_timer = None
        self._cancelled_revision = None
        self._operation_kind = kind
        self._operation_message = message
        self._operation_started = time.monotonic()
        self._active_revision = self.document.revision
        self._cancel_requested = False
        self.engine.begin_operation()
        self._set_busy(True)

    def cancel_translation(self):
        if not self.busy or self._operation_kind != "translation" or self._cancel_requested:
            return
        self._cancel_requested = True
        self.engine.cancel()
        self._refresh_ui()

    def _refresh_save_state(self):
        if self.file_path:
            changed = self._saved_snapshot != self.document.to_dict()
            self.save_state.set(f"草稿已保存 · {self.file_path.name}" + (" 有未保存修改" if changed else " 已保存"))
        else:
            self.save_state.set("草稿已保存 · 尚未保存为文件")

    def _write_draft(self):
        data = self.document.to_dict()
        data["_editor"] = {"file_path": str(self.file_path) if self.file_path else None}
        write_json(self.data_dir / "draft.json", data)

    def _remember_file(self, path):
        selected = str(Path(path).resolve())
        self.settings["recent_files"] = [selected] + [p for p in self.settings["recent_files"] if p != selected][:7]
        self._persist_settings()

    def _populate_recent(self):
        self.recent_menu.delete(0, "end")
        for name in self.settings["recent_files"]:
            path = Path(name)
            self.recent_menu.add_command(label=f"{path.name}  —  {path.parent}",
                                         command=lambda p=path: self._open_path(p))
        if not self.settings["recent_files"]:
            self.recent_menu.add_command(label="暂无最近文档", state="disabled")

    def show_recovery(self):
        if not self.busy:
            RecoveryDialog(self.root, self.data_dir / "recovery",
                           lambda path: self._open_path(path, recovery=True))

    def copy_translation(self, side="en"):
        self.editor.get_source()
        text = self.document.source if side == "en" else self.document.target
        if not text:
            self.status.set("所选语言暂无内容，请先输入或翻译。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.set("已复制英文。" if side == "en" else "已复制中文。")
        if self.document.pending:
            self.status.set(self.status.get() + "两侧内容尚未同步。")

    def export_translation(self, side="en"):
        self.editor.get_source()
        text = self.document.source if side == "en" else self.document.target
        language = "英文" if side == "en" else "中文"
        if not text:
            self.status.set("所选语言暂无内容，请先输入或翻译。")
            return
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", self.document.title).strip(" .") or "未命名文档"
        path = filedialog.asksaveasfilename(parent=self.root, title=f"导出{language}", initialfile=name + f"_{language}.txt",
            defaultextension=".txt", filetypes=[("文本文件", "*.txt"), ("Markdown", "*.md")])
        if path:
            try:
                with Path(path).open("w", encoding="utf-8", newline="") as output:
                    output.write(text)
                self.status.set(f"{language}已导出。" + ("两侧内容尚未同步。" if self.document.pending else ""))
            except OSError as error:
                messagebox.showerror("导出失败", str(error), parent=self.root)

    def show_font_menu(self):
        try:
            self.font_menu.tk_popup(self.font_button.winfo_rootx(),
                                    self.font_button.winfo_rooty() + self.font_button.winfo_height())
        finally:
            self.font_menu.grab_release()

    def _change_font_size(self):
        self.settings["font_size"] = self.font_size.get()
        self.editor.set_font_size(self.font_size.get())
        self._persist_settings()

    def show_search(self):
        self.search_bar.pack(fill="x", before=self.editor)
        self.search_bar.activate()

    def _shortcut(self, action):
        action()
        return "break"

    def _callback_error(self, error_type, error, traceback):
        self.diagnostics.error("界面回调异常", error.with_traceback(traceback))
        self.status.set("界面操作出错，可按 Ctrl+Shift+L 查看排查日志。")

    def _engine_status(self):
        if self.engine.requires_connection:
            return f"{self.engine.name} 未连接"
        return self.engine.connection_label()

    def _activate_engine(self):
        if self._startup_timer is not None:
            self.root.after_cancel(self._startup_timer)
            self._startup_timer = None
        if self._closed:
            return
        if self.engine.requires_connection:
            self.connect()
        else:
            self.connection.set(self.engine.connection_label())
            self._schedule_auto()

    def _load_draft(self):
        path = self.data_dir / "draft.json"
        if not path.exists():
            return
        try:
            self._set_document(load_document(path))
            metadata = json.loads(path.read_text(encoding="utf-8")).get("_editor", {})
            saved_path = metadata.get("file_path") if isinstance(metadata, dict) else None
            if isinstance(saved_path, str):
                selected = Path(saved_path)
                if selected.is_absolute() and selected.suffix.lower() == ".json":
                    self.file_path = selected
                    try:
                        self._saved_snapshot = load_document(selected).to_dict()
                    except (OSError, ValueError, TypeError, KeyError):
                        self._saved_snapshot = None
            self._refresh_save_state()
            self.status.set("已恢复上次文档。" if not self.document.pending else "已恢复文档，有修改待同步。")
        except (OSError, ValueError, TypeError, KeyError):
            self.status.set("上次草稿无法读取；可用“打开”选择其他文档。")

    def _set_document(self, document):
        self.document = document
        self._failed_revision = None
        self._cancelled_revision = None
        self.reset_session_button.pack_forget()
        self.conflict_bar.pack_forget()
        self.title.set(document.title)
        self.editor.set_source(document.source)
        self.editor.sync_texts(document.source, document.target, document.baseline, document.segments, highlight=False)
        self.editor.source.edit_reset()
        self.editor.target.edit_reset()
        self.editor.highlight_source(document.baseline)
        self.editor.highlight_target(document.target_baseline)
        self.search_bar.refresh()
        self._refresh_ui()

    def _title_changed(self, *_):
        self.document.title = self.title.get().strip() or "未命名文档"
        self._schedule_save()

    def _edited(self, text):
        if not self.document.edit(text):
            return
        self._after_edit()

    def _edited_target(self, text):
        if not self.document.edit_target(text):
            return
        self._after_edit()

    def _after_edit(self):
        if self.document.structure_source:
            # User edits (including Undo) take precedence over imported layout.
            self.document.needs_structure = False
        self._failed_revision = None
        self._cancelled_revision = None
        self.conflict_bar.pack_forget()
        self.editor.highlight_source(self.document.baseline)
        self.editor.highlight_target(self.document.target_baseline)
        self.search_bar.refresh()
        if not self.busy:
            self.status.set("内容已修改，等待双向同步。" if self.document.pending else "中英文已同步。")
        self._schedule_save()
        self._schedule_auto()
        self._refresh_ui()

    def _schedule_auto(self):
        if self._auto_timer:
            self.root.after_cancel(self._auto_timer)
            self._auto_timer = None
        if self.auto.get() and self.document.pending and self._failed_revision != self.document.revision:
            self._auto_timer = self.root.after(800, self._auto_translate)

    def _auto_translate(self):
        self._auto_timer = None
        if not self.busy and self.auto.get() and self._failed_revision != self.document.revision:
            self.translate()

    def _schedule_save(self):
        if self._save_timer:
            self.root.after_cancel(self._save_timer)
        self._save_timer = self.root.after(500, self._autosave)
        self.save_state.set("正在保存草稿…")

    def _autosave(self):
        self._save_timer = None
        try:
            self._write_draft()
            self._refresh_save_state()
        except OSError as error:
            self.save_state.set("草稿保存失败，请手动保存文件")
            self.status.set(f"草稿保存失败：{error}")
            self.diagnostics.error("草稿保存失败", error)

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in (self.open_button, self.translate_button, self.reset_session_button, self.settings_button):
            button.configure(state=state)
        # Saving and diagnostics remain available while a request is in flight.
        for index in (0, 3):
            self.document_menu.entryconfigure(index, state=state)
        for index in (6, 7):
            self.document_menu.entryconfigure(index, state=state)
        if busy:
            self.progress.pack(side="left", padx=14)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()
        self._refresh_ui()

    def connect(self):
        if self.busy or self._closed or not self.engine.requires_connection:
            return
        self._begin_operation("connect", "正在连接翻译引擎…")
        self.connection.set(f"正在连接 {self.engine.name}…")
        self.jobs.submit("connect", None, self.engine.connect)

    def translate(self):
        if self.busy:
            return
        # Read immediately: toolbar clicks can precede delivery of <<Modified>>.
        self.document.edit(self.editor.get_source())
        try:
            request = prepare_request(self.document)
        except ConflictingEdits as error:
            self._failed_revision = self.document.revision
            self.status.set(str(error))
            self.conflict_bar.pack(fill="x", before=self.editor)
            self._refresh_ui()
            return
        except Exception as error:
            self._failed_revision = self.document.revision
            self.status.set(str(error))
            self.diagnostics.error("准备翻译失败", error, revision=self.document.revision)
            self._refresh_ui()
            return
        if request is None:
            self.status.set("中英文已同步。")
            return
        message = {"structure": "正在整理段落…",
                         "initial": "正在首次翻译…",
                          "revise": "正在更新修改对应的译文…",
                          "sentence": "正在翻译修改涉及的整句…"}[request.mode]
        if request.mode in ("revise", "sentence") and self.engine.fast_updates:
            message = "正在用 Fast 更新译文…"
        message += "（中文 → 英文）" if request.source_language == "zh" else "（英文 → 中文）"
        self._begin_operation("translation", message)
        self.jobs.submit("translation", request, lambda: self.engine.translate_document(request))

    def _resolve_conflicts(self, side):
        if self.busy:
            return
        self.editor.get_source()
        try:
            save_document(self.data_dir / "recovery" / f"{self.document.id}.json", self.document)
        except OSError as error:
            messagebox.showerror("备份失败", str(error), parent=self.root)
            return
        resolve_conflicts(self.document, side)
        self.editor.sync_texts(self.document.source, self.document.target, self.document.baseline, self.document.segments)
        self.conflict_bar.pack_forget()
        self._failed_revision = None
        self._schedule_save()
        self.translate()

    def reset_session(self):
        if self.busy or not self.engine.supports_session_reset:
            return
        document_id = self.document.id
        self._begin_operation("reset_session", "正在重建翻译会话…")
        self.status.set("正在重建翻译会话，文档和现有译文保持原样…")
        self.jobs.submit("reset_session", document_id,
                         lambda: self.engine.recreate_session(document_id))

    def _poll(self):
        if self._closed:
            return
        while True:
            try:
                result = self.jobs.results.get_nowait()
            except queue.Empty:
                break
            cancelled = self._cancel_requested
            self._cancel_requested = False
            self._set_busy(False)
            self._operation_kind = None
            if cancelled:
                self._failed_revision = self.document.revision
                self._cancelled_revision = self.document.revision
                self.status.set("已取消更新，原文和现有译文已保留；可点击更新继续。")
                self._schedule_auto()
                self._refresh_ui()
                continue
            if result.error:
                if (result.kind == "translation" and result.context.document_id == self.document.id
                        and result.context.revision != self.document.revision
                        and not isinstance(result.error, SessionBusyError)):
                    self.status.set("旧版本翻译未完成，等待最新修改。")
                    self._failed_revision = None
                    self._schedule_auto()
                    continue
                self._failed_revision = self.document.revision
                self.status.set(str(result.error))
                if isinstance(result.error, SessionBusyError):
                    self.reset_session_button.pack(side="right", padx=(0, 8))
                if result.kind == "connect":
                    self.connection.set(f"{self.engine.name} 未连接")
                continue
            if result.kind == "connect":
                self.connection.set(result.value)
                self._failed_revision = None
                self._schedule_auto()
                continue
            if result.kind == "reset_session":
                self.reset_session_button.pack_forget()
                self._failed_revision = None
                self.connection.set(self.engine.connection_label())
                self.status.set("已重建会话，现有译文已保留；点击“翻译 / 更新”继续。")
                self._schedule_auto()
                continue
            try:
                self.document.edit(self.editor.get_source())
                accepted = apply_response(self.document, result.context, result.value)
                self.diagnostics.record(("文档结构初始化完成" if result.context.mode == "structure"
                                         else "双语整句更新已提交") if accepted else "忽略过期结果",
                    "INFO" if accepted else "WARNING", document_id=result.context.document_id,
                    revision=result.context.revision, current_revision=self.document.revision,
                    attempt=getattr(self.engine, "_diagnostic_context", {}).get("attempt"))
                if accepted:
                    if result.context.mode == "structure":
                        self.editor.sync_texts(self.document.source, self.document.target,
                                               self.document.baseline, self.document.segments)
                        self.search_bar.refresh()
                        self._failed_revision = None
                        self._schedule_save()
                        # A manual import/translation follows through even with auto disabled.
                        self.translate()
                        continue
                    self.reset_session_button.pack_forget()
                    self.editor.sync_texts(self.document.source, self.document.target,
                                           self.document.baseline, self.document.segments)
                    if self.document.source != self.document.baseline:
                        self.editor.highlight_source(self.document.baseline)
                    if self.document.target != self.document.target_baseline:
                        self.editor.highlight_target(self.document.target_baseline)
                    self.search_bar.refresh()
                    self.status.set("中英文已同步" if result.context.mode == "initial"
                                    else ("已在本地同步空白或删除" if self.engine.last_timing.get("outcome") == "local"
                                          else "中英文已按整句同步"))
                    elapsed = self.engine.last_timing.get("total_seconds")
                    if isinstance(elapsed, (int, float)):
                        self.status.set(f"{self.status.get()} · {elapsed:.1f} 秒")
                    if self.engine.last_timing.get("outcome") != "local":
                        self.connection.set(self.engine.connection_label())
                    self._failed_revision = None
                    self._schedule_save()
                    if self.document.pending:
                        # Finish both directions of the accepted snapshot, also in manual mode.
                        self.translate()
                        continue
                else:
                    self.status.set("已忽略旧版本结果，等待最新修改。")
                self._schedule_auto()
            except Exception as error:
                self._failed_revision = self.document.revision
                self.status.set(str(error))
                self.diagnostics.error("结构初始化校验失败" if result.context.mode == "structure"
                                       else "双语整句校验失败", error,
                    document_id=result.context.document_id, revision=result.context.revision,
                    attempt=getattr(self.engine, "_diagnostic_context", {}).get("attempt"),
                    validation=getattr(error, "details", {}), response=result.value)
        self._refresh_ui()
        self._poll_timer = self.root.after(100, self._poll)

    def _persist_settings(self):
        self.settings.update(auto=self.auto.get())
        try:
            write_json(self.data_dir / "settings.json", self.settings)
        except OSError as error:
            self.status.set(f"设置保存失败：{error}")

    def show_settings(self):
        if self.busy:
            return
        SettingsDialog(self.root, self.settings, self._apply_settings, api_key=self.deepseek_key)

    def show_logs(self):
        if self._log_window is None or not self._log_window.winfo_exists():
            self._log_window = LogWindow(self.root, self.diagnostics)
        self._log_window.deiconify()
        self._log_window.lift()
        self._log_window.focus_set()

    def _apply_settings(self, settings):
        settings = dict(settings)
        key = settings.pop("deepseek_api_key", self.deepseek_key)
        updated = {**self.settings, "auto": self.auto.get(), **settings}
        fields = ("provider", "timeout")
        if "codex" in (self.settings["provider"], updated["provider"]):
            fields += ("codex_path", "fast_updates")
        engine_changed = (any(self.settings.get(field) != updated.get(field) for field in fields)
                          or (updated["provider"] == "deepseek" and key != self.deepseek_key))
        if key != self.deepseek_key or self._key_error:
            try:
                save_deepseek_key(self.data_dir / "deepseek_key.bin", key)
            except CredentialError as error:
                messagebox.showerror("设置", str(error), parent=self.root)
                return False
        self.deepseek_key = key
        self.diagnostics.register_secret(key)
        self._key_error = None
        if engine_changed:
            self.engine.close()
        self.settings.update(settings)
        self.auto.set(updated.get("auto", self.auto.get()))
        self._persist_settings()
        if engine_changed:
            self.engine = self._new_engine()
            self.diagnostics.record("切换翻译引擎", provider=self.engine.name)
            self.reset_session_button.pack_forget()
            self._failed_revision = None
            self.connection.set(self._engine_status())
            self._activate_engine()
        else:
            self._schedule_auto()
        return True

    def new(self):
        if self.busy:
            return
        # Keep an independent recovery copy before replacing the active draft.
        if self.document.source or self.document.target:
            try:
                save_document(self.data_dir / "recovery" / f"{self.document.id}.json", self.document)
            except OSError as error:
                messagebox.showerror("新建", f"当前文档备份失败：{error}", parent=self.root)
                return
        self.file_path = None
        self._saved_snapshot = None
        self._set_document(Document())
        self._schedule_save()
        self.status.set("新文档 · 左侧中文，右侧英文，两边都可编辑。")
        self.editor.target.focus_set()

    def open(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self.root, title="打开文档",
                    filetypes=[("文档", "*.json *.txt *.md *.markdown"), ("JSON 文档", "*.json"),
                               ("Markdown", "*.md *.markdown"), ("原文文本", "*.txt")])
        if not path:
            return
        self._open_path(Path(path))

    def _open_path(self, selected, recovery=False):
        if self.busy:
            return False
        try:
            selected = Path(selected)
            if selected.suffix.lower() in (".txt", ".md", ".markdown"):
                with selected.open(encoding="utf-8-sig", newline="") as stream:
                    source = stream.read()
                document = Document(title=selected.stem, source=source,
                                     needs_structure=self.settings["organize_imports"])
                han = len(re.findall(r"[\u3400-\u9fff]", source))
                if han and han * 3 >= len(re.findall(r"[A-Za-z]", source)):
                    document.source, document.target_text = "", source
            else:
                document = load_document(selected)
                if self.settings["organize_imports"] and not document.segments and document.source.strip():
                    document.needs_structure = True
            if self.document.source or self.document.target:
                save_document(self.data_dir / "recovery" / f"{self.document.id}.json", self.document)
            self.file_path = selected if selected.suffix.lower() == ".json" and not recovery else None
            self._saved_snapshot = document.to_dict() if self.file_path else None
            self._set_document(document)
            self._schedule_save()
            self._schedule_auto()
            self.status.set("已导入新文档，首次翻译前将初始化整体结构。" if document.needs_structure else "文档已打开。")
            if recovery:
                self.status.set("备份已恢复，可继续编辑或保存为文件。")
            else:
                self._remember_file(selected)
            return True
        except (OSError, ValueError, TypeError, KeyError) as error:
            messagebox.showerror("打开失败", str(error), parent=self.root)
            return False

    def save(self):
        self.document.edit(self.editor.get_source())
        selected = self.file_path
        if selected is None:
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", self.document.title).strip(" .") or "双语文档"
            path = filedialog.asksaveasfilename(parent=self.root, title="保存文档",
                defaultextension=".json", initialfile=name + ".json", filetypes=[("双语文档", "*.json")])
            if not path:
                return
            selected = Path(path)
        try:
            save_document(selected, self.document)
            self.file_path = selected
            self._saved_snapshot = self.document.to_dict()
            self._remember_file(selected)
            self._schedule_save()
            self.status.set("文档已保存。")
        except OSError as error:
            messagebox.showerror("保存失败", str(error), parent=self.root)

    def close(self):
        if self._closed:
            return
        self.document.edit(self.editor.get_source())
        try:
            self._write_draft()
        except OSError as error:
            if not messagebox.askyesno("草稿保存失败", f"{error}\n仍然关闭窗口吗？", parent=self.root):
                return
        self._closed = True
        self.progress.stop()
        for timer in (self._auto_timer, self._save_timer, self._poll_timer, self._startup_timer):
            if timer:
                self.root.after_cancel(timer)
        self.jobs.close()
        self.engine.cancel()
        self.engine.close()
        self.diagnostics.record("应用关闭")
        if self._log_window is not None and self._log_window.winfo_exists():
            self._log_window.close()
        self.root.destroy()

