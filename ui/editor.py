"""Two continuous native text panes; backend owns structure and edit analysis."""

from bisect import bisect_right
from difflib import SequenceMatcher
import tkinter as tk
from tkinter import ttk

from backend.diff import source_opcodes
from backend.markdown import analyze_markdown
from backend.paragraphs import ParagraphPair, markdown_pairs
from .appearance import LINE


class BilingualEditor(ttk.Frame):
    def __init__(self, parent, on_change, font_size=16, on_target_change=None):
        super().__init__(parent)
        self._on_change = on_change
        self._on_target_change = on_target_change or (lambda text: None)
        self._last_target_text = ""
        self._placeholders = {}
        self._destroyed = False
        self._suppress_change = False
        self._style_timer = self._sync_timer = None
        self._alignment_timer = None
        self._alignment_tags = []
        self._baseline = self._highlight_baseline = self._last_text = ""
        self._segments = ()
        self._pairs = [ParagraphPair("", "")]
        self._scroll_owner = None
        self.blocks = ()
        self._utf16 = int(self.tk.call("string", "length", "🙂")) == 2
        self.font_size = font_size
        self.columnconfigure(0, weight=1, uniform="panes")
        self.columnconfigure(2, weight=1, uniform="panes")
        self.rowconfigure(0, weight=1)
        self.target, self.target_scrollbar = self._pane("中文", 0, False)
        tk.Frame(self, background=LINE, width=1).grid(row=0, column=1, sticky="ns", pady=(16, 8))
        self.source, self.source_scrollbar = self._pane("英文", 2, True)
        # The linked panes share the one visible scrollbar at the far right.
        self.target_scrollbar.pack_forget()
        self.set_font_size(font_size)
        self._scroll_owner = self.target
        self.source.configure(yscrollcommand=lambda a, b: self._view_changed(self.source, a, b))
        self.target.configure(yscrollcommand=lambda a, b: self._view_changed(self.target, a, b))
        self.source_scrollbar.configure(command=lambda *args: self._scroll(self.source, *args))
        self.target_scrollbar.configure(command=lambda *args: self._scroll(self.target, *args))
        for widget in (self.source, self.target):
            widget.bind("<<Modified>>", self._modified)
            for event in ("<Control-z>", "<<Undo>>"):
                widget.bind(event, lambda event, w=widget: self._undo(False, w))
            for event in ("<Control-y>", "<Control-Shift-Z>", "<<Redo>>"):
                widget.bind(event, lambda event, w=widget: self._undo(True, w))
        self._refresh_styles()
        self._update_placeholder("")
        self._update_placeholder("", self.target)

    def _pane(self, heading, column, editable):
        frame = ttk.Frame(self, style="Pane.TFrame")
        frame.grid(row=0, column=column, sticky="nsew")
        ttk.Label(frame, text=heading, style="PaneHeading.TLabel", padding=(30, 20, 30, 10)).pack(fill="x")
        body = ttk.Frame(frame, style="Pane.TFrame")
        body.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(body, orient="vertical", style="Reader.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        widget = tk.Text(body, wrap="word" if editable else "char", width=1, height=1, undo=True,
                         autoseparators=True, maxundo=-1, background="white",
                         foreground="#20242d", insertbackground="#2f6edb",
                         selectbackground="#deebff", selectforeground="#20242d", relief="flat", borderwidth=0,
                         highlightthickness=0, padx=30, pady=8, spacing1=2, spacing2=7, spacing3=7,
                         font=("Georgia" if editable else "SimSun", self.font_size), exportselection=False)
        widget.pack(fill="both", expand=True)
        placeholder = tk.Label(widget, text=("在这里输入或粘贴英文\n\n修改英文后，会更新左侧对应的中文。" if editable else
                                             "在这里输入或粘贴中文\n\n修改中文后，会更新右侧对应的英文。"),
                                        background="white", foreground="#858b98", justify="left",
                                        font=("Microsoft YaHei UI", 11), cursor="xterm")
        placeholder.bind("<Button-1>", lambda event: widget.focus_set())
        self._placeholders[widget] = placeholder
        self.placeholder = placeholder
        widget.tag_configure("heading1", foreground="#1e222a")
        widget.tag_configure("heading2", foreground="#1e222a")
        widget.tag_configure("heading3", foreground="#1e222a")
        widget.tag_configure("quote", foreground="#66717e")
        widget.tag_configure("marker", foreground="#4876a0")
        widget.tag_configure("code", font=("Consolas", 11), background="#f4f5f7")
        widget.tag_configure("inline_code", font=("Consolas", 11), background="#f4f5f7")
        widget.tag_configure("paragraph_end", spacing3=16)
        widget.tag_configure("changed", background="#edf4ff" if editable else "#e6f0ff")
        widget.tag_raise("changed")
        widget.bind("<Control-a>", lambda event, w=widget: self._select_all(w))
        for event in ("<MouseWheel>", "<Button-1>", "<KeyPress>", "<FocusIn>"):
            widget.bind(event, lambda event, w=widget: self._take_scroll(w), add="+")
        widget.bind("<Configure>", lambda event: self._schedule_alignment())
        return widget, scrollbar

    def set_font_size(self, size):
        """Reflow the existing widgets without touching text, selection or undo."""
        self.font_size = size
        for widget, family in ((self.source, "Georgia"), (self.target, "SimSun")):
            widget.configure(font=(family, size))
            for tag, scale in (("heading1", 1.65), ("heading2", 1.3), ("heading3", 1.1)):
                widget.tag_configure(tag, font=(family, round(size * scale), "bold"),
                                     spacing1=8, spacing3=12)
            widget.tag_configure("strong", font=(family, size, "bold"))
            for tag in ("code", "inline_code"):
                widget.tag_configure(tag, font=("Consolas", max(10, size - 2)))
        self._schedule_alignment()

    def _index(self, text, offset):
        prefix = text[:offset]
        line = prefix.count("\n") + 1
        last = prefix.rsplit("\n", 1)[-1]
        column = len(last.encode("utf-16-le")) // 2 if self._utf16 else len(last)
        return f"{line}.{column}"

    @staticmethod
    def _select_all(widget):
        widget.tag_add("sel", "1.0", "end-1c")
        widget.mark_set("insert", "end-1c")
        return "break"

    def _take_scroll(self, widget):
        self._scroll_owner = widget

    def _scroll(self, widget, *args):
        self._take_scroll(widget)
        widget.yview(*args)
        self._schedule_sync()

    def _view_changed(self, widget, first, last):
        scrollbar = self.source_scrollbar if widget is self.source else self.target_scrollbar
        scrollbar.set(first, last)
        if widget is self._scroll_owner:
            self._schedule_sync()

    def _schedule_sync(self):
        if self._destroyed:
            return
        if self._sync_timer is None:
            self._sync_timer = self.after_idle(self._sync_scroll)

    def _schedule_alignment(self):
        if not self._destroyed and self._alignment_timer is None:
            self._alignment_timer = self.after_idle(self._align_paragraphs)

    def _align_paragraphs(self):
        """Equalize paired blocks using layout tags, never document characters.

        Only shared logical-line boundaries are safe anchors. Pending edits can
        remove a boundary on one side; keep that block with its neighbour until
        a shared boundary is available again.
        """
        self._alignment_timer = None
        # A withdrawn/new window still has a one-pixel text area. Measuring it
        # would wrap a large document character by character for no useful work;
        # Configure will request alignment once the real width is available.
        if any(w.winfo_width() <= 60 for w in (self.source, self.target)):
            return
        owner = self._scroll_owner or self.source
        visible = owner.index("@0,0")
        total = max(1, self._pixels(owner, "1.0", "end"))
        within = round(owner.yview()[0] * total) - self._pixels(owner, "1.0", visible)
        for widget in (self.source, self.target):
            for tag in self._alignment_tags:
                widget.tag_delete(tag)
        self._alignment_tags = []
        texts = [w.get("1.0", "end-1c") for w in (self.source, self.target)]
        if self._segments and all(texts):
            anchors = [("1.0", "1.0")]
            offsets = [0, 0]
            previous = [0, 0]
            for pair in self._pairs[:-1]:
                offsets[0] += len(pair.source)
                offsets[1] += len(pair.target)
                if all(previous[i] < n < len(texts[i]) and texts[i][n-1] == "\n"
                       for i, n in enumerate(offsets)):
                    anchors.append(tuple(self._index(text, n) for text, n in zip(texts, offsets)))
                    previous = offsets.copy()
            anchors.append(("end", "end"))
            widgets = (self.source, self.target)
            # Force Tk's lazy line metrics once before measuring all blocks.
            for widget in widgets:
                widget.tk.call(widget._w, "count", "-update", "-ypixels", "1.0", "end")
            heights = [tuple(self._pixels(w, a[i], b[i]) for i, w in enumerate(widgets))
                       for a, b in zip(anchors, anchors[1:])]
            for number, (end, height) in enumerate(zip(anchors[1:], heights)):
                if height[0] == height[1]:
                    continue
                side = 0 if height[0] < height[1] else 1
                widget = widgets[side]
                start = widget.index(f"{end[side]} -1c linestart")
                # Tk takes line spacing from tags at the first character.
                spacing = int(widget.cget("spacing3"))
                for tag in widget.tag_names(start):
                    value = widget.tag_cget(tag, "spacing3")
                    if value:
                        spacing = int(value)
                tag = f"alignment_{number}"
                self._alignment_tags.append(tag)
                widget.tag_configure(tag, spacing3=spacing + abs(height[0] - height[1]))
                widget.tag_add(tag, start, end[side])
            for widget in widgets:
                widget.tk.call(widget._w, "count", "-update", "-ypixels", "1.0", "end")
        # Keep the reader's current line and partial-line scroll offset stable.
        total = max(1, self._pixels(owner, "1.0", "end"))
        owner.yview_moveto(max(0, self._pixels(owner, "1.0", visible) + within) / total)
        self._schedule_sync()

    @staticmethod
    def _pixels(widget, start, end):
        count = widget.count(start, end, "ypixels")
        return count[0] if count else 0

    def _sync_scroll(self):
        if self._sync_timer is not None:
            self.after_cancel(self._sync_timer)
        self._sync_timer = None
        owner = self._scroll_owner or self.source
        peer = self.target if owner is self.source else self.source
        if self._alignment_timer is not None:
            return
        if self._segments:
            total = max(1, self._pixels(owner, "1.0", "end"))
            peer_total = max(1, self._pixels(peer, "1.0", "end"))
            pixels = round(owner.yview()[0] * total)
            if abs(peer.yview()[0] * peer_total - pixels) >= 1:
                peer.yview_moveto(pixels / peer_total)
            return
        if not self._pairs:
            return
        english = owner is self.source
        starts, peer_starts = [0], [0]
        for pair in self._pairs:
            starts.append(starts[-1] + len(pair.source if english else pair.target))
            peer_starts.append(peer_starts[-1] + len(pair.target if english else pair.source))
        visible = owner.index("@0,0")
        position = len(owner.get("1.0", visible))
        number = min(len(self._pairs)-1, max(0, bisect_right(starts, position)-1))
        text = owner.get("1.0", "end-1c")
        peer_text = peer.get("1.0", "end-1c")
        start = self._index(text, starts[number])
        end = self._index(text, starts[number+1])
        peer_start = self._index(peer_text, peer_starts[number])
        peer_end = self._index(peer_text, peer_starts[number+1])
        height = max(1, self._pixels(owner, start, end))
        progress = max(0, min(1, self._pixels(owner, start, visible) / height))
        pixels = self._pixels(peer, "1.0", peer_start) + progress * self._pixels(peer, peer_start, peer_end)
        total = max(1, self._pixels(peer, "1.0", "end"))
        fraction = pixels / total
        if abs(peer.yview()[0] - fraction) > 0.001:
            peer.yview_moveto(fraction)

    def _emit_change(self):
        text = self.source.get("1.0", "end-1c")
        self._update_placeholder(text)
        if not self._suppress_change and text != self._last_text:
            self._last_text = text
            self._on_change(text)
            self._schedule_styles()
        return text

    def _modified(self, _event=None):
        widget = _event.widget if _event is not None else self.source
        if widget.edit_modified():
            widget.edit_modified(False)
            (self._emit_change if widget is self.source else self._emit_target_change)()

    def _emit_target_change(self):
        text = self.target.get("1.0", "end-1c")
        self._update_placeholder(text, self.target)
        if not self._suppress_change and text != self._last_target_text:
            self._last_target_text = text
            self._on_target_change(text)
            self._schedule_styles()
        return text

    def _schedule_styles(self):
        if self._style_timer is not None:
            self.after_cancel(self._style_timer)
        self._style_timer = self.after(160, self._refresh_styles)

    def _style(self, widget, text):
        blocks, spans = analyze_markdown(text)
        for tag in ("heading1", "heading2", "heading3", "strong", "quote", "marker", "code", "inline_code",
                    "paragraph_end"):
            widget.tag_remove(tag, "1.0", "end")
        for index, block in enumerate(blocks):
            if block.kind == "blank" or (block.kind == "list" and index + 1 < len(blocks)
                                          and blocks[index + 1].kind == "list"):
                continue
            end = block.start + len(text[block.start:block.end].rstrip())
            if end > block.start:
                widget.tag_add("paragraph_end", self._index(text, end - 1), self._index(text, end))
        for span in spans:
            widget.tag_add(span.style, self._index(text, span.start), self._index(text, span.end))
        widget.tag_raise("changed")
        return blocks

    def _refresh_styles(self):
        if self._style_timer is not None:
            self.after_cancel(self._style_timer)
        self._style_timer = None
        text = self.source.get("1.0", "end-1c")
        self.blocks = self._style(self.source, text)
        self._style(self.target, self.target.get("1.0", "end-1c"))
        if self._segments:
            from backend.bilingual import projections
            from backend.models import Document, Segment
            en, zh, _, _ = projections(Document(source=text, baseline=self._baseline,
                segments=list(self._segments), target_text=self.target.get("1.0", "end-1c")))
            paired = tuple(Segment(e.id, e.source, z.source) for e, z in zip(en, zh))
            self._pairs = markdown_pairs(text, text, paired)
        else:
            self._pairs = [ParagraphPair(text, self.target.get("1.0", "end-1c"))]
        self._schedule_alignment()

    def get_source(self):
        # Toolbar commands can run before delivery of <<Modified>>.
        self._emit_target_change()
        return self._emit_change()

    def get_target(self):
        self._emit_change()
        return self._emit_target_change()

    def _undo(self, redo, widget=None):
        widget = widget or self.source
        self._take_scroll(widget)
        try:
            (widget.edit_redo if redo else widget.edit_undo)()
        except tk.TclError:
            return "break"
        (self._emit_change if widget is self.source else self._emit_target_change)()
        widget.see("insert")
        return "break"

    def focus_source(self):
        self.source.focus_set()

    def _update_placeholder(self, text, widget=None):
        widget = widget or self.source
        placeholder = self._placeholders[widget]
        if text:
            placeholder.place_forget()
        else:
            placeholder.place(x=0, y=0)

    def apply_source_layout(self, text):
        """Apply paragraph layout as one undoable edit, preserving Tk marks."""
        old = self.source.get("1.0", "end-1c")
        view = self.source.yview()[0]
        autoseparators = self.source.cget("autoseparators")
        self._suppress_change = True
        try:
            self.source.edit_separator()
            self.source.configure(autoseparators=False)
            for kind, a, b, c, d in reversed(SequenceMatcher(None, old, text, autojunk=False).get_opcodes()):
                if kind != "equal":
                    self.source.replace(self._index(old, a), self._index(old, b), text[c:d])
            self.source.edit_separator()
            self.source.edit_modified(False)
            self._last_text = text
            self.source.yview_moveto(view)
        finally:
            self.source.configure(autoseparators=autoseparators)
            self._suppress_change = False
        self._update_placeholder(text)
        self._refresh_styles()

    def set_source(self, text):
        self._suppress_change = True
        try:
            self.source.delete("1.0", "end")
            self.source.insert("1.0", text)
            self.source.edit_reset()
            self.source.edit_modified(False)
            self.source.mark_set("insert", "1.0")
            self.source.yview_moveto(0)
            self._last_text = text
            self._update_placeholder(text)
        finally:
            self._suppress_change = False
        self._segments = ()
        self._baseline = self._highlight_baseline = ""
        self.show_target("", highlight=False)

    def highlight_source(self, baseline):
        self._highlight_baseline = baseline
        text = self.source.get("1.0", "end-1c")
        self.source.tag_remove("changed", "1.0", "end")
        for kind, _, _, a, b in source_opcodes(baseline, text):
            if kind != "equal" and a != b:
                self.source.tag_add("changed", self._index(text, a), self._index(text, b))

    def highlight_target(self, baseline):
        text = self.target.get("1.0", "end-1c")
        self.target.tag_remove("changed", "1.0", "end")
        for kind, _, _, a, b in source_opcodes(baseline, text):
            if kind != "equal" and a != b:
                self.target.tag_add("changed", self._index(text, a), self._index(text, b))

    def sync_texts(self, english, chinese, baseline, segments, highlight=True):
        self._replace_text(self.source, english, highlight)
        self._replace_text(self.target, chinese, highlight)
        self._baseline, self._segments = baseline, tuple(segments)
        self._refresh_styles()

    def update_translation(self, baseline, segments, highlight=True):
        self.get_source()
        self._baseline = baseline
        self._segments = tuple(segments)
        self.show_target("".join(segment.target for segment in segments), highlight)

    def show_target(self, text, highlight=True):
        self._replace_text(self.target, text, highlight)
        self._refresh_styles()

    def _replace_text(self, widget, text, highlight=True):
        old = widget.get("1.0", "end-1c")
        changes = list(source_opcodes(old, text))
        suppress = self._suppress_change
        self._suppress_change = True
        autoseparators = widget.cget("autoseparators")
        try:
            if old != text:
                widget.edit_separator()
            widget.configure(autoseparators=False)
            for kind, a, b, c, d in reversed(changes):
                if kind != "equal":
                    widget.replace(self._index(old, a), self._index(old, b), text[c:d])
            if old != text:
                widget.edit_separator()
            widget.edit_modified(False)
            if widget is self.source:
                self._last_text = text
            else:
                self._last_target_text = text
        finally:
            widget.configure(autoseparators=autoseparators)
            self._suppress_change = suppress
        widget.tag_remove("changed", "1.0", "end")
        for kind, _, _, a, b in changes:
            if highlight and kind != "equal" and a != b:
                widget.tag_add("changed", self._index(text, a), self._index(text, b))
        self._update_placeholder(text, widget)
        # Tags and alignment metadata change; the English widget never changes.
        if self._style_timer is not None:
            self.after_cancel(self._style_timer)
        self._style_timer = None

    def destroy(self):
        self._destroyed = True
        for timer in (self._style_timer, self._sync_timer, self._alignment_timer):
            if timer is not None:
                self.after_cancel(timer)
        self._style_timer = self._sync_timer = None
        self._alignment_timer = None
        super().destroy()

