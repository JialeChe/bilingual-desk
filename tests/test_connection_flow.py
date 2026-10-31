import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from backend.models import Document
from backend.providers.base import TranslationEngine
from ui.settings import SettingsDialog
from ui.window import MainWindow


class StubEngine(TranslationEngine):
    def __init__(self, requires_connection=False):
        self.name = "测试引擎"
        self.requires_connection = requires_connection
        self.last_timing = {}
        self.connect = Mock(return_value="测试引擎 已连接")
        self.close = Mock()

    def translate(self, request):
        raise AssertionError("Unexpected generation")


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class ConnectionFlowTests(unittest.TestCase):
    def test_only_local_engines_connect_on_startup(self):
        for requires_connection in (False, True):
            with self.subTest(requires_connection=requires_connection), tempfile.TemporaryDirectory() as folder:
                root = tk.Tk()
                root.withdraw()
                engine = StubEngine(requires_connection)
                with patch.object(MainWindow, "_new_engine", return_value=engine):
                    window = MainWindow(root, Path(folder))
                root.update_idletasks()
                window.jobs.submit = Mock()
                try:
                    root.after(300, root.quit)
                    root.mainloop()
                    if requires_connection:
                        window.jobs.submit.assert_called_once_with("connect", None, engine.connect)
                        self.assertTrue(window.busy)
                    else:
                        window.jobs.submit.assert_not_called()
                        self.assertFalse(window.busy)
                        self.assertEqual(window.connection.get(), engine.name)
                    self.assertIsNone(window._startup_timer)
                finally:
                    window.close()

    def test_main_window_exposes_auto_translation_without_connect_buttons(self):
        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            root.withdraw()
            with patch.object(MainWindow, "_new_engine", return_value=StubEngine()):
                window = MainWindow(root, Path(folder), auto_connect=False)
            root.update_idletasks()
            try:
                controls = list(descendants(root))
                self.assertEqual(window.settings["provider"], "deepseek")
                buttons = [w.cget("text") for w in controls if w.winfo_class() == "TButton"]
                self.assertFalse(any("连接" in label for label in buttons))
                checkboxes = [w for w in controls if w.winfo_class() == "TCheckbutton"]
                self.assertEqual([w.cget("text") for w in checkboxes], ["自动翻译"])
                checkboxes[0].invoke()
                self.assertFalse(window.auto.get())
                self.assertFalse(json.loads(Path(folder, "settings.json").read_text())["auto"])
            finally:
                window.close()

    def test_auto_setting_reschedules_pending_document_without_restarting_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            directory.joinpath("settings.json").write_text('{"auto": false}', encoding="utf-8")
            root = tk.Tk()
            root.withdraw()
            engine = StubEngine(True)
            with patch.object(MainWindow, "_new_engine", return_value=engine):
                window = MainWindow(root, directory, auto_connect=False)
            root.update_idletasks()
            try:
                window._set_document(Document(source="Hello."))
                before = window.document.to_dict()
                self.assertTrue(window._apply_settings({"auto": True}))
                self.assertTrue(window.auto.get())
                self.assertIsNotNone(window._auto_timer)
                self.assertIs(window.engine, engine)
                engine.connect.assert_not_called()
                engine.close.assert_not_called()
                self.assertTrue(json.loads(directory.joinpath("settings.json").read_text())["auto"])
                self.assertTrue(window._apply_settings({"auto": False}))
                self.assertFalse(window.auto.get())
                self.assertIsNone(window._auto_timer)
                self.assertEqual(window.document.to_dict(), before)
                self.assertFalse(json.loads(directory.joinpath("settings.json").read_text())["auto"])
            finally:
                window.close()
            root = tk.Tk()
            root.withdraw()
            with patch.object(MainWindow, "_new_engine", return_value=StubEngine()):
                restored = MainWindow(root, directory, auto_connect=False)
            root.update_idletasks()
            try:
                self.assertFalse(restored.auto.get())
                self.assertEqual(restored.document.to_dict(), before)
            finally:
                restored.close()

    def test_settings_dialog_contains_auto_and_saves_its_value(self):
        root = tk.Tk()
        captured = []
        try:
            dialog = SettingsDialog(root, {"auto": False}, captured.append,
                                    api_key="dummy-settings-test-key")
            labels = [w.cget("text") for w in descendants(dialog)
                      if w.winfo_class() in ("TCheckbutton", "TButton")]
            self.assertIn("自动更新", labels)
            self.assertIn("保存", labels)
            self.assertNotIn("保存并连接", labels)
            self.assertFalse(dialog.auto.get())
            self.assertEqual(dialog.provider.get(), "DeepSeek API · Flash")
            dialog.auto.set(True)
            dialog._save()
            self.assertTrue(captured[0]["auto"])
            self.assertEqual(captured[0]["provider"], "deepseek")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
