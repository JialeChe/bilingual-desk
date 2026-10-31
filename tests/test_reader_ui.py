import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from backend.models import Document, Segment
from tests.test_connection_flow import StubEngine
from ui.window import MainWindow


class ReaderUiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.engine = StubEngine()
        with patch.object(MainWindow, "_new_engine", return_value=self.engine):
            self.window = MainWindow(self.root, Path(self.folder.name), auto_connect=False)
        self.window.auto.set(False)
        source, target = "🙂 Hello.\n\nhello.", "🙂 你好。\n\n你好。"
        self.window._set_document(Document(source=source, baseline=source,
                                          segments=[Segment("a", source, target)]))
        self.root.update()

    def tearDown(self):
        self.window.close()
        self.folder.cleanup()

    def test_search_finds_both_panes_without_editing_them_and_clears_on_close(self):
        before = self.window.document.to_dict()
        self.window.show_search()
        search = self.window.search_bar
        search.query.set("HELLO")
        self.assertEqual(len(search.matches), 2)
        search.next()
        widget, start, end = search.matches[search.current]
        self.assertEqual(widget.get(start, end), "Hello")
        search.next()
        self.assertEqual(search.current, 1)
        search.next(-1)
        self.assertEqual(search.current, 0)
        search.query.set("你好")
        search.next()
        widget, start, end = search.matches[search.current]
        self.assertIs(widget, self.window.editor.target)
        self.assertEqual(widget.get(start, end), "你好")
        self.assertEqual(widget.cget("state"), "normal")
        search.query.set("[.*")  # Search text is literal, not a regular expression.
        self.assertEqual(len(search.matches), 0)
        search.hide()
        for widget in (self.window.editor.source, self.window.editor.target):
            self.assertFalse(widget.tag_ranges("search_match"))
            self.assertFalse(widget.tag_ranges("search_current"))
        self.assertEqual(self.window.document.to_dict(), before)

    def test_font_change_preserves_selection_and_undo_and_restores_after_restart(self):
        source = self.window.editor.source
        source.insert("end-1c", " More.")
        source.edit_separator()
        self.window.editor.get_source()
        source.mark_set("insert", "1.4")
        source.tag_add("sel", "1.3", "1.8")
        insertion, selection = source.index("insert"), source.tag_ranges("sel")
        self.window.font_size.set(20)
        self.window._change_font_size()
        self.root.update()
        self.assertEqual(source.index("insert"), insertion)
        self.assertEqual(source.tag_ranges("sel"), selection)
        self.assertIn("More.", self.window.editor.get_source())
        self.window.editor._undo(False)
        self.assertNotIn("More.", self.window.editor.get_source())
        self.engine.close.assert_not_called()
        self.assertEqual(json.loads(Path(self.folder.name, "settings.json").read_text())["font_size"], 20)
        self.window.close()
        self.root = tk.Tk()
        self.root.withdraw()
        with patch.object(MainWindow, "_new_engine", return_value=StubEngine()):
            self.window = MainWindow(self.root, Path(self.folder.name), auto_connect=False)
        self.assertEqual(self.window.editor.font_size, 20)
        self.assertEqual(self.window.font_size.get(), 20)

    def test_busy_requests_keep_logs_and_saving_available_in_menu(self):
        self.window._set_busy(True)
        self.assertEqual(self.window.document_menu.entrycget(0, "state"), "disabled")
        self.assertEqual(self.window.document_menu.entrycget(1, "state"), "normal")
        self.assertEqual(self.window.document_menu.entrycget(3, "state"), "disabled")
        self.assertEqual(self.window.document_menu.entrycget(4, "state"), "normal")
        self.window.document_menu.invoke(4)
        self.assertTrue(self.window._log_window.winfo_exists())

    def test_search_shortcut_works_from_source_and_escape_closes_it(self):
        self.root.deiconify()
        self.root.update()
        source = self.window.editor.source
        source.focus_force()
        self.root.update()
        source.event_generate("<Control-f>")
        self.root.update()
        self.assertTrue(self.window.search_bar.winfo_ismapped())
        self.assertIs(self.root.focus_get(), self.window.search_bar.entry)
        self.window.search_bar.entry.event_generate("<Escape>")
        self.root.update()
        self.assertFalse(self.window.search_bar.winfo_ismapped())


if __name__ == "__main__":
    unittest.main()
