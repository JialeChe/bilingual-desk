import tkinter as tk
import unittest

from backend.models import Segment
from ui.editor import BilingualEditor


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.changes = []
        self.target_changes = []
        self.editor = BilingualEditor(self.root, self.changes.append, on_target_change=self.target_changes.append)
        self.editor.pack(fill="both", expand=True)
        self.source = "Three days.\n\nBudget unchanged."
        self.units = (Segment("a", "Three days.\n\n", "三天。\n\n"),
                      Segment("b", "Budget unchanged.", "预算不变。"))
        self.editor.set_source(self.source)
        self.editor.update_translation(self.source, self.units, highlight=False)
        self.root.update()

    def tearDown(self):
        self.editor.destroy()
        self.root.destroy()

    def test_only_two_continuous_text_panes_and_two_headings(self):
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets = list(descendants(self.editor))
        self.assertEqual(sum(isinstance(w, tk.Text) for w in widgets), 2)
        labels = [w.cget("text") for w in widgets if w.winfo_class() == "TLabel"]
        self.assertEqual(labels, ["中文", "英文"])
        self.assertEqual(self.editor.get_source(), self.source)
        self.assertEqual(self.editor.target.get("1.0", "end-1c"), "三天。\n\n预算不变。")
        self.assertEqual(self.changes, [])

    def test_edit_undo_redo_survive_translation_update(self):
        widget = self.editor.source
        widget.replace("1.0", "1.5", "Five")
        widget.edit_separator()
        self.editor.get_source()
        self.editor.update_translation(self.source, self.units)
        widget.insert("end-1c", " Yes.")
        widget.edit_separator()
        self.root.update()
        self.assertEqual(self.editor.get_source(), self.source.replace("Three", "Five") + " Yes.")
        self.editor._undo(False)
        self.assertEqual(self.editor.get_source(), self.source.replace("Three", "Five"))
        self.editor._undo(False)
        self.assertEqual(self.editor.get_source(), self.source)
        self.editor._undo(True)
        self.assertEqual(self.editor.get_source(), self.source.replace("Three", "Five"))
        self.assertEqual(self.editor.target.get("1.0", "end-1c"), "三天。\n\n预算不变。")

    def test_cross_paragraph_selection_can_replace_multiple_sentences(self):
        widget = self.editor.source
        widget.tag_add("sel", "1.0", "3.6")
        widget.replace("sel.first", "sel.last", "New ")
        self.assertEqual(self.editor.get_source(), "New  unchanged.")
        self.editor._undo(False)
        self.assertEqual(self.editor.get_source(), self.source)

    def test_select_all_and_delete_covers_entire_document(self):
        widget = self.editor.source
        self.editor._select_all(widget)
        self.assertEqual(widget.get("sel.first", "sel.last"), self.source)
        widget.delete("sel.first", "sel.last")
        self.assertEqual(self.editor.get_source(), "")
        self.editor._undo(False)
        self.assertEqual(self.editor.get_source(), self.source)

    def test_typing_and_translation_never_recreate_english_or_move_caret_selection(self):
        widget = self.editor.source
        widget.mark_set("insert", "3.6")
        widget.tag_add("sel", "3.0", "3.6")
        insertion, selection = widget.index("insert"), widget.tag_ranges("sel")
        identity = widget.winfo_id()
        updated = (Segment("a", self.units[0].source, "五天。\n\n"), self.units[1])
        self.editor.update_translation(self.source, updated)
        self.editor._refresh_styles()
        self.root.update()
        self.assertIs(self.editor.source, widget)
        self.assertEqual(widget.winfo_id(), identity)
        self.assertEqual(widget.index("insert"), insertion)
        self.assertEqual(widget.tag_ranges("sel"), selection)
        self.assertEqual(self.editor.get_source(), self.source)

    def test_enter_then_style_update_preserves_trailing_newline_and_next_input(self):
        self.editor.set_source("hello")
        widget = self.editor.source
        widget.mark_set("insert", "end-1c")
        widget.insert("insert", "\n")
        self.editor.get_source()
        self.editor._refresh_styles()
        self.assertEqual(widget.get("1.0", "insert"), "hello\n")
        widget.insert("insert", "New line.")
        self.assertEqual(self.editor.get_source(), "hello\nNew line.")

    def test_target_patch_highlights_only_updated_characters_and_supports_edit_undo(self):
        updated = (Segment("a", self.units[0].source, "五天。\n\n"), self.units[1])
        self.editor.update_translation(self.source, updated)
        widget = self.editor.target
        ranges = widget.tag_ranges("changed")
        self.assertEqual(widget.get(ranges[0], ranges[1]), "五")
        self.assertEqual(len(ranges), 2)
        widget.edit_separator()
        widget.insert("1.0", "新增")
        widget.edit_separator()
        self.editor.get_source()
        self.assertEqual(self.target_changes[-1], "新增五天。\n\n预算不变。")
        self.editor._undo(False, widget)
        self.assertEqual(widget.get("1.0", "end-1c"), "五天。\n\n预算不变。")

    def test_markdown_styling_changes_only_tags_not_document_or_undo_history(self):
        text = "# Title\n\n- **First** item\n- Second item\n\n> A quote\n"
        self.editor.set_source(text)
        widget = self.editor.source
        widget.mark_set("insert", "3.0")
        widget.insert("insert", "New ")
        widget.edit_separator()
        self.editor.get_source()
        self.editor._refresh_styles()
        self.assertTrue(widget.tag_ranges("heading1"))
        self.assertTrue(widget.tag_ranges("strong"))
        self.assertTrue(widget.tag_ranges("marker"))
        self.assertTrue(widget.tag_ranges("quote"))
        self.assertEqual(self.editor.get_source(), text.replace("- **First**", "New - **First**"))
        self.editor._undo(False)
        self.assertEqual(self.editor.get_source(), text)

    def test_unicode_highlights_and_translation_preserve_exact_raw_text(self):
        text = "🙂 hello\r\n\r\nBudget unchanged."
        self.editor.set_source(text)
        self.editor.highlight_source(text.replace("hello", "world"))
        widget = self.editor.source
        ranges = widget.tag_ranges("changed")
        marked = "".join(widget.get(a, b) for a, b in zip(ranges[::2], ranges[1::2]))
        self.assertTrue(marked)
        self.assertNotIn("🙂", marked)
        self.assertEqual(self.editor.get_source(), text)
        self.editor.show_target("🙂 你好\r\n\r\n预算不变。")
        self.editor.show_target("🙂 您好\r\n\r\n预算不变。")
        self.assertEqual(self.editor.target.get("1.0", "end-1c"), "🙂 您好\r\n\r\n预算不变。")

    def test_toolbar_flush_reports_latest_edit_once(self):
        self.editor.source.insert("end-1c", " Yes.")
        self.assertEqual(self.editor.get_source(), self.source + " Yes.")
        self.root.update()
        self.editor.get_source()
        self.assertEqual(self.changes, [self.source + " Yes."])

    def test_scrolling_follows_corresponding_paragraph_in_both_directions(self):
        errors = []
        self.root.report_callback_exception = lambda *args: errors.append(args)
        self.root.geometry("900x420")
        self.root.deiconify()
        units = tuple(Segment(str(i), f"Paragraph {i}: " + "English words. " * 25 + "\n\n",
                              f"第{i}段：" + "中文内容。" * 6 + "\n\n") for i in range(20))
        source = "".join(s.source for s in units)
        self.editor.set_source(source)
        self.editor.update_translation(source, units, highlight=False)
        self.root.update()
        self.editor._take_scroll(self.editor.source)
        self.editor.source.yview("17.0")
        self.root.update()
        self.assertEqual(self.editor.source.index("@0,0").split(".")[0], "17")
        self.assertEqual(self.editor.target.index("@0,0").split(".")[0], "17")
        self.editor._take_scroll(self.editor.target)
        self.editor.target.yview("7.0")
        self.root.update()
        self.assertEqual(self.editor.source.index("@0,0").split(".")[0], "7")
        self.assertEqual(self.editor.target.index("@0,0").split(".")[0], "7")
        self.assertEqual(errors, [])

    def _uneven_document(self):
        self.root.geometry("900x420")
        self.root.deiconify()
        units = tuple(Segment(str(i), f"Paragraph {i}: " + "English words. " * (24 if i % 2 else 2) + "\n\n",
                              f"第{i}段：" + "中文内容。" * (2 if i % 2 else 30) + "\n\n")
                      for i in range(24))
        source = "".join(s.source for s in units)
        self.editor.set_source(source)
        self.editor.update_translation(source, units, highlight=False)
        self.root.update()
        return units

    def _assert_paragraph_heights(self):
        editor = self.editor
        offsets = [0, 0]
        texts = [editor.get_source(), editor.get_target()]
        for pair in editor._pairs:
            indices = [editor._index(text, n) for text, n in zip(texts, offsets)]
            heights = [editor._pixels(w, "1.0", index)
                       for w, index in zip((editor.source, editor.target), indices)]
            self.assertLessEqual(abs(heights[0] - heights[1]), 1, (indices, heights))
            offsets[0] += len(pair.source)
            offsets[1] += len(pair.target)
        self.assertEqual(editor._pixels(editor.source, "1.0", "end"),
                         editor._pixels(editor.target, "1.0", "end"))

    def test_uneven_paragraphs_align_after_resize_and_font_change(self):
        units = self._uneven_document()
        self._assert_paragraph_heights()
        for geometry, size in (("640x420", 20), ("1200x600", 12)):
            self.root.geometry(geometry)
            self.editor.set_font_size(size)
            self.root.update()
            self._assert_paragraph_heights()
        self.assertEqual(self.editor.get_source(), "".join(s.source for s in units))
        self.assertEqual(self.editor.get_target(), "".join(s.target for s in units))
        self.assertEqual(self.changes, [])
        self.assertEqual(self.target_changes, [])

    def test_padding_preserves_both_edits_selection_and_undo(self):
        self._uneven_document()
        for widget in (self.editor.source, self.editor.target):
            original = widget.get("1.0", "end-1c")
            widget.edit_separator()
            widget.insert("3.0", "新增长内容 extra words " * 20)
            widget.edit_separator()
            widget.mark_set("insert", "3.8")
            widget.tag_add("sel", "3.1", "3.5")
            self.editor.get_source()
            self.editor._refresh_styles()
            self.root.update()
            self._assert_paragraph_heights()
            self.assertEqual(widget.index("insert"), "3.8")
            self.assertEqual(tuple(map(str, widget.tag_ranges("sel"))), ("3.1", "3.5"))
            self.editor._undo(False, widget)
            self.editor._refresh_styles()
            self.root.update()
            self.assertEqual(widget.get("1.0", "end-1c"), original)
            self._assert_paragraph_heights()

    def test_partial_line_scroll_and_bottom_stay_aligned(self):
        self._uneven_document()
        for owner in (self.editor.source, self.editor.target):
            self.editor._take_scroll(owner)
            for fraction in (0.1537, 0.4831, 1.0, 0.0):
                owner.yview_moveto(fraction)
                self.root.update()
                pixels = [round(w.yview()[0] * self.editor._pixels(w, "1.0", "end"))
                          for w in (self.editor.source, self.editor.target)]
                self.assertLessEqual(abs(pixels[0] - pixels[1]), 1)

    def test_markdown_and_pending_line_breaks_align_at_shared_boundaries(self):
        self.root.geometry("900x500")
        self.root.deiconify()
        units = (
            Segment("h", "# A heading that wraps " * 4 + "\n", "# 标题\n"),
            Segment("a", "- " + "list words " * 20 + "\n", "- 列表\n"),
            Segment("b", "> Quote\n\n", "> " + "引用文字" * 40 + "\n\n"),
            Segment("c", "```\nprint('hello')\n```\n\n", "```\nprint('你好')\n```\n\n"),
            Segment("d", "Final paragraph.", "最后一段。"))
        source = "".join(s.source for s in units)
        self.editor.set_source(source)
        self.editor.update_translation(source, units, highlight=False)
        self.root.update()
        self._assert_paragraph_heights()
        for widget in (self.editor.source, self.editor.target):
            widget.insert("2.4", "\nextra line ")
            self.editor.get_source()
            self.editor._refresh_styles()
            self.root.update()
            self._assert_paragraph_heights()
        # Replacing translated text also recalculates paragraph padding.
        self.editor.sync_texts(source, "".join(s.target for s in units), source, units)
        self.root.update()
        self._assert_paragraph_heights()


if __name__ == "__main__":
    unittest.main()

