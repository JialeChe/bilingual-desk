import json
from pathlib import Path
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import patch

import httpx

from backend.diagnostics import Diagnostics
from backend.jobs import JobRunner
from backend.models import Document, Segment, TargetPatch, TranslationResponse
from backend.providers.deepseek import DeepSeekEngine
from backend.translation import PatchError, locate_patch
from backend.bilingual import prepare_request
from tests.test_connection_flow import StubEngine
from tests.test_deepseek import stream_response
from ui.window import MainWindow


class DiagnosticTests(unittest.TestCase):
    def test_secrets_are_redacted_in_nested_values_raw_json_and_traces(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Diagnostics(Path(folder) / "debug.jsonl")
            key = "dummy-test-secret"
            log.register_secret(key)
            log.record("request", headers={"Authorization": "Bearer other-secret"}, api_key=key,
                       raw='{"access_token": "another-secret", "text": "dummy-test-secret"}',
                       text="Bearer bearer-secret sk-abcdef123456")
            try:
                raise ValueError("echo " + key)
            except ValueError as error:
                log.error("failure", error)
            exported = Path(folder) / "export.txt"
            log.export(exported)
            for content in (log.format(), log.path.read_text(encoding="utf-8"),
                            exported.read_text(encoding="utf-8")):
                for secret in (key, "other-secret", "another-secret", "bearer-secret", "sk-abcdef123456"):
                    self.assertNotIn(secret, content)
                self.assertIn("ValueError", content)
                self.assertIn("[已隐藏]", content)

    def test_concurrent_recording_retention_and_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Diagnostics(Path(folder) / "debug.jsonl", capacity=20, max_bytes=4096)
            threads = [threading.Thread(target=lambda: [log.record("event", text="你好" * 20)
                                                       for _ in range(40)]) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            entries = log.snapshot()
            self.assertEqual(len(entries), 20)
            self.assertEqual([e["sequence"] for e in entries], list(range(101, 121)))
            for path in (log.path, log.path.with_suffix(".jsonl.1")):
                self.assertTrue(path.exists())
                self.assertLessEqual(path.stat().st_size, 4096)
                for line in path.read_text(encoding="utf-8").splitlines():
                    self.assertIn("event", json.loads(line))
            entries[0]["details"]["text"] = "modified"
            self.assertNotEqual(log.snapshot()[0]["details"]["text"], "modified")

    def test_failed_disk_write_keeps_memory_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Diagnostics(Path(folder))  # A directory cannot be opened as a file.
            log.record("retained", text="测试")
            self.assertTrue(log.write_error)
            self.assertEqual(log.snapshot()[0]["event"], "retained")

    def test_worker_failure_captures_sanitized_trace(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Diagnostics(Path(folder) / "debug.jsonl")
            log.register_secret("test-secret")
            jobs = JobRunner(log)
            try:
                def fail():
                    raise RuntimeError("test-secret failure")
                jobs.submit("translation", None, fail)
                result = jobs.results.get(timeout=3)
                self.assertIsInstance(result.error, RuntimeError)
                self.assertIn("Traceback", log.format())
                self.assertNotIn("test-secret", log.format())
            finally:
                jobs.close()

    def test_model_raw_output_retained_even_when_parser_rejects_it(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Diagnostics(Path(folder) / "debug.jsonl")
            raw = "invalid JSON dummy-key"
            engine = DeepSeekEngine("dummy-key", transport=httpx.MockTransport(
                lambda request: stream_response(raw)))
            engine.set_diagnostics(log)
            try:
                with self.assertRaises(Exception):
                    engine.translate(prepare_request(Document(source="Hello.")))
                entry = next(e for e in log.snapshot() if e["event"] == "模型原始返回")
                self.assertIn("invalid JSON", entry["details"]["raw"])
                self.assertNotIn("dummy-key", log.format())
                self.assertIn("翻译请求", log.format())
            finally:
                engine.close()

    def test_anchor_diagnostics_distinguish_zero_and_multiple_matches(self):
        for before, count in (("不存在", 0), ("你好", 2)):
            with self.subTest(count=count):
                with self.assertRaises(PatchError) as caught:
                    locate_patch("你好你好", TargetPatch("a", before, "再见"))
                self.assertEqual(caught.exception.details["match_count"], count)
                self.assertEqual(caught.exception.details["current_target"], "你好你好")


class LogWindowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.engine = StubEngine()
        with patch.object(MainWindow, "_new_engine", return_value=self.engine):
            self.window = MainWindow(self.root, Path(self.folder.name), auto_connect=False)
        self.window.auto.set(False)
        self.root.update()

    def tearDown(self):
        self.window.close()
        self.folder.cleanup()

    def test_shortcut_single_window_busy_live_filter_reopen_and_shutdown(self):
        self.window._set_busy(True)
        self.root.focus_force()
        self.root.update()
        self.root.event_generate("<Control-Shift-L>")
        self.root.update()
        dialog = self.window._log_window
        self.assertIsNotNone(dialog)
        self.window.show_logs()
        self.assertIs(self.window._log_window, dialog)
        self.window.diagnostics.record("synthetic-error", "ERROR", detail="定位失败")
        dialog._refresh()
        self.assertIn("synthetic-error", dialog.text.get("1.0", "end"))
        dialog.errors_only.set(True)
        dialog._refresh()
        self.assertNotIn("应用启动", dialog.text.get("1.0", "end"))
        self.assertIn("定位失败", dialog.text.get("1.0", "end"))
        with patch.object(dialog, "clipboard_clear") as clear, patch.object(dialog, "clipboard_append") as append:
            dialog.copy_all()
            clear.assert_called_once()
            self.assertIn("synthetic-error", append.call_args.args[0])
        path = Path(self.folder.name) / "export.txt"
        with patch("ui.logs.filedialog.asksaveasfilename", return_value=str(path)):
            dialog.export()
        self.assertIn("synthetic-error", path.read_text(encoding="utf-8"))
        dialog.close()
        self.assertIsNone(dialog._timer)
        self.window.show_logs()
        self.assertIsNot(dialog, self.window._log_window)

    def test_missing_sentence_logs_evidence_and_keeps_document_unchanged(self):
        from backend.jobs import JobResult
        doc = Document(source="Hello again.", baseline="Hello.",
                       segments=[Segment("a", "Hello.", "你好你好")])
        self.window._set_document(doc)
        request = prepare_request(doc)
        response = TranslationResponse(doc.id, doc.revision)
        before = doc.to_dict()
        self.window.jobs.results.put(JobResult("translation", request, response))
        self.root.after_cancel(self.window._poll_timer)
        self.window._poll()
        self.assertEqual(doc.to_dict(), before)
        entry = next(e for e in self.window.diagnostics.snapshot() if e["event"] == "双语整句校验失败")
        self.assertEqual(entry["details"]["validation"]["expected_ids"], ["a"])
        self.assertEqual(entry["details"]["validation"]["received_ids"], [])

    def test_gui_callback_errors_are_recorded(self):
        try:
            raise ValueError("synthetic UI error")
        except ValueError as error:
            self.root.report_callback_exception(type(error), error, error.__traceback__)
        self.assertIn("界面回调异常", self.window.diagnostics.format())
        self.assertIn("Ctrl+Shift+L", self.window.status.get())


if __name__ == "__main__":
    unittest.main()
