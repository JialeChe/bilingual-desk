import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock

from backend.credentials import load_deepseek_key
from backend.models import Document, Segment
from backend.providers.codex import CodexEngine
from backend.providers.deepseek import DeepSeekEngine
from backend.translation import prepare_request
from ui.window import MainWindow


@unittest.skipUnless(os.name == "nt", "Windows encrypted settings")
class ProviderSwitchTests(unittest.TestCase):
    def test_switch_keeps_alignment_and_stores_key_outside_plain_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "settings.json").write_text('{"provider": "codex"}', encoding="utf-8")
            root = tk.Tk()
            root.withdraw()
            window = MainWindow(root, directory, auto_connect=False)
            try:
                self.assertIsInstance(window.engine, CodexEngine)
                window.auto.set(False)
                window.connect = Mock()
                document = Document(source="Five days.", baseline="Three days.",
                                    segments=[Segment("a", "Three days.", "三天。")])
                window._set_document(document)
                original = document.to_dict()
                self.assertTrue(window._apply_settings({"provider": "deepseek",
                                                       "deepseek_api_key": "dummy-ui-test-key"}))
                self.assertIsInstance(window.engine, DeepSeekEngine)
                self.assertFalse(hasattr(window, "connect_button"))
                window.connect.assert_not_called()
                self.assertEqual(window.connection.get(), window.engine.connection_label())
                self.assertFalse(window.engine.supports_session_reset)
                self.assertEqual(document.to_dict(), original)
                self.assertEqual(prepare_request(document).mode, "revise")
                settings = (directory / "settings.json").read_text(encoding="utf-8")
                self.assertNotIn("dummy-ui-test-key", settings)
                self.assertNotIn("deepseek_api_key", json.loads(settings))
                self.assertEqual(load_deepseek_key(directory / "deepseek_key.bin"), "dummy-ui-test-key")
                self.assertTrue(window._apply_settings({"provider": "codex"}))
                self.assertIsInstance(window.engine, CodexEngine)
                window.connect.assert_called_once()
                self.assertEqual(document.to_dict(), original)
            finally:
                window.close()

    def test_restart_restores_api_without_codex_session_dependency(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            root = tk.Tk()
            root.withdraw()
            window = MainWindow(root, directory, auto_connect=False)
            window.connect = Mock()
            window._apply_settings({"provider": "deepseek", "deepseek_api_key": "dummy-ui-test-key"})
            window.close()
            (directory / "codex_sessions.json").write_text("bad json", encoding="utf-8")
            root = tk.Tk()
            root.withdraw()
            restored = MainWindow(root, directory, auto_connect=False)
            try:
                self.assertIsInstance(restored.engine, DeepSeekEngine)
                self.assertEqual(restored.deepseek_key, "dummy-ui-test-key")
                self.assertEqual(restored.settings["provider"], "deepseek")
            finally:
                restored.close()


if __name__ == "__main__":
    unittest.main()
