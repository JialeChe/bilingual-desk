import asyncio
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import httpx

from backend.jobs import JobResult
from backend.models import Document, Segment, TargetPatch, TranslationResponse
from backend.providers.base import TranslationCancelled
from backend.providers.deepseek import DeepSeekEngine
from backend.providers.codex import CodexEngine
from backend.providers.rpc import CodexRPC
from backend.storage import save_document, load_document
from backend.translation import prepare_request
from tests.test_connection_flow import StubEngine
from tests.test_deepseek import stream_response
from ui.history import RecoveryDialog
from ui.window import MainWindow


class UserExperienceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self._create_window()

    def _create_window(self):
        self.root = tk.Tk()
        self.root.withdraw()
        with patch.object(MainWindow, "_new_engine", return_value=StubEngine()):
            self.window = MainWindow(self.root, self.directory, auto_connect=False)
        self.window.auto.set(False)
        self.root.update()

    def tearDown(self):
        self.window.close()
        self.folder.cleanup()

    def _synced_document(self):
        source = "Three days."
        document = Document(title="计划", source=source, baseline=source,
                            segments=[Segment("a", source, "三天。")])
        self.window._set_document(document)
        return document

    def _poll(self):
        self.root.after_cancel(self.window._poll_timer)
        self.window._poll()

    def test_cancel_discards_even_valid_late_response_and_does_not_auto_restart(self):
        document = self._synced_document()
        self.window.editor.source.replace("1.0", "1.5", "Five")
        self.window.editor.get_source()
        self.window.auto.set(True)
        self.window.jobs.submit = Mock()
        self.window.translate()
        request = self.window.jobs.submit.call_args.args[1]
        self.assertEqual(self.window.translate_button.cget("text"), "取消翻译")
        self.window.cancel_translation()
        # Even a scheduled automatic update must stay paused after cancellation.
        self.window._schedule_auto()
        response = TranslationResponse(request.document_id, request.revision,
                                       patches=(TargetPatch("a", "三", "五"),))
        self.window.jobs.results.put(JobResult("translation", request, response))
        self._poll()
        self.assertEqual(document.target, "三天。")
        self.assertIn("已取消", self.window.sync_state.get())
        self.assertIsNone(self.window._auto_timer)
        self.window._auto_translate()
        self.assertEqual(self.window.jobs.submit.call_count, 1)
        self.window.translate()
        self.assertEqual(self.window.jobs.submit.call_count, 2)
        self.assertFalse(self.window.engine._cancel_event.is_set())

    def test_sync_failure_and_retry_are_visible_without_replacing_old_translation(self):
        document = self._synced_document()
        self.window.editor.source.insert("end-1c", " More.")
        self.window.editor.get_source()
        self.assertIn("尚未同步", self.window.sync_state.get())
        self.window.jobs.submit = Mock()
        self.window.translate()
        request = self.window.jobs.submit.call_args.args[1]
        self.window.jobs.results.put(JobResult("translation", request, error=RuntimeError("网络中断")))
        self._poll()
        self.assertIn("更新失败", self.window.sync_state.get())
        self.assertEqual(self.window.translate_button.cget("text"), "重试翻译")
        self.assertEqual(document.target, "三天。")

    def test_restart_restores_file_destination_and_distinguishes_unsaved_edits(self):
        self._synced_document()
        destination = self.directory / "计划.json"
        with patch("ui.window.filedialog.asksaveasfilename", return_value=str(destination)):
            self.window.save()
        self.window.editor.source.insert("end-1c", " More.")
        self.window.editor.get_source()
        self.window._autosave()
        self.assertIn("有未保存修改", self.window.save_state.get())
        self.window.close()
        self._create_window()
        self.assertEqual(self.window.file_path, destination)
        self.assertIn("有未保存修改", self.window.save_state.get())
        with patch("ui.window.filedialog.asksaveasfilename") as chooser:
            self.window.save()
        chooser.assert_not_called()
        self.window._autosave()
        self.assertNotIn("有未保存修改", self.window.save_state.get())
        self.assertIn("More.", destination.read_text(encoding="utf-8"))

    def test_failed_save_does_not_remember_an_unsaved_destination(self):
        self._synced_document()
        with patch("ui.window.filedialog.asksaveasfilename", return_value=str(self.directory / "bad.json")), \
                patch("ui.window.save_document", side_effect=OSError("disk full")), \
                patch("ui.window.messagebox.showerror"):
            self.window.save()
        self.assertIsNone(self.window.file_path)
        self.assertEqual(self.window.settings["recent_files"], [])

    def test_import_preserves_raw_layout_by_default_and_remembers_recent_file(self):
        path = self.directory / "source.md"
        raw = "# Title\r\nHello.  World.\r\n"
        path.write_bytes(raw.encode("utf-8"))
        self.assertTrue(self.window._open_path(path))
        self.assertEqual(self.window.document.source, raw)
        self.assertFalse(self.window.document.needs_structure)
        self.assertEqual(prepare_request(self.window.document).mode, "initial")
        self.window._populate_recent()
        self.assertIn("source.md", self.window.recent_menu.entrycget(0, "label"))

    def test_recovery_restores_selected_document_and_backs_up_current_contents(self):
        current = self._synced_document()
        recovered = Document(title="旧草稿", source="A saved draft.")
        backup = self.directory / "recovery" / f"{recovered.id}.json"
        save_document(backup, recovered)
        dialog = RecoveryDialog(self.root, backup.parent,
                                lambda path: self.window._open_path(path, recovery=True))
        self.root.update()
        self.assertEqual(len(dialog.paths), 1)
        dialog.restore()
        self.assertEqual(self.window.document.id, recovered.id)
        self.assertIsNone(self.window.file_path)
        self.assertTrue((backup.parent / f"{current.id}.json").exists())

    def test_copy_and_export_preserve_chinese_and_warn_about_pending_changes(self):
        document = self._synced_document()
        self.window.editor.source.insert("end-1c", " More.")
        self.window.editor.get_source()
        before = document.to_dict()
        self.window.copy_translation("zh")
        self.assertEqual(self.root.clipboard_get(), "三天。")
        self.assertIn("尚未同步", self.window.status.get())
        path = self.directory / "中文.md"
        with patch("ui.window.filedialog.asksaveasfilename", return_value=str(path)):
            self.window.export_translation("zh")
        self.assertEqual(path.read_text(encoding="utf-8"), "三天。")
        self.assertIn("尚未同步", self.window.status.get())
        self.assertEqual(document.to_dict(), before)

    def test_paragraph_layout_is_one_undo_step_and_preserves_previous_edits(self):
        editor = self.window.editor
        editor.set_source("First. Second. Third.")
        editor.source.insert("end-1c", " Fourth.")
        editor.source.edit_separator()
        before = editor.get_source()
        editor.source.mark_set("insert", "1.3")
        editor.source.tag_add("sel", "1.0", "1.3")
        formatted = "First.\n\nSecond.\n\nThird.\n\nFourth."
        editor.apply_source_layout(formatted)
        self.assertEqual(editor.get_source(), formatted)
        self.assertEqual(editor.source.index("insert"), "1.3")
        self.assertEqual(editor.source.get("sel.first", "sel.last"), "Fir")
        editor._undo(False)
        self.assertEqual(editor.get_source(), before)
        editor._undo(False)
        self.assertEqual(editor.get_source(), "First. Second. Third.")

    def test_chinese_edit_updates_english_and_default_copy_uses_english(self):
        document = self._synced_document()
        self.window.editor.target.replace("1.0", "1.1", "五")
        self.window.jobs.submit = Mock()
        self.window.translate()
        request = self.window.jobs.submit.call_args.args[1]
        self.assertEqual(request.source_language, "zh")
        self.window.jobs.results.put(JobResult("translation", request,
            TranslationResponse(document.id, document.revision, translations={"a": "Five days."})))
        self._poll()
        self.assertEqual(self.window.editor.source.get("1.0", "end-1c"), "Five days.")
        self.assertEqual(self.window.editor.target.get("1.0", "end-1c"), "五天。")
        self.assertFalse(document.pending)
        self.window.copy_translation()
        self.assertEqual(self.root.clipboard_get(), "Five days.")

    def test_conflict_choice_backs_up_both_manual_edits_before_starting(self):
        document = self._synced_document()
        self.window.editor.target.replace("1.0", "1.1", "五")
        self.window.editor.source.replace("1.0", "1.5", "Six")
        self.window.jobs.submit = Mock()
        self.window.translate()
        self.window.jobs.submit.assert_not_called()
        self.assertEqual(self.window.conflict_bar.winfo_manager(), "pack")
        self.window._resolve_conflicts("zh")
        backup = load_document(self.directory / "recovery" / f"{document.id}.json")
        self.assertEqual((backup.target, backup.source), ("五天。", "Six days."))
        request = self.window.jobs.submit.call_args.args[1]
        self.assertEqual(request.source_language, "zh")
        self.assertEqual(self.window.editor.source.get("1.0", "end-1c"), "Three days.")

    def test_import_chinese_goes_left_and_save_flushes_chinese_edit(self):
        path = self.directory / "中文.md"
        raw = "# 中文文档\r\n这是待翻译的内容。\r\n"
        path.write_bytes(raw.encode("utf-8"))
        self.assertTrue(self.window._open_path(path))
        self.assertEqual(self.window.document.target, raw)
        self.assertEqual(self.window.document.source, "")
        self.window.editor.target.insert("end-1c", "新增。")
        output = self.directory / "saved.json"
        with patch("ui.window.filedialog.asksaveasfilename", return_value=str(output)):
            self.window.save()
        self.assertEqual(load_document(output).target, raw + "新增。")

    def test_settings_button_stays_upper_left_and_opens_directly(self):
        self.root.deiconify()
        self.root.geometry("800x480")
        self.root.update()
        settings = self.window.settings_button
        self.assertLess(settings.winfo_rootx(), self.window.document_button.winfo_rootx())
        self.assertLess(settings.winfo_rooty(), self.window.editor.winfo_rooty())
        self.assertLess(self.window.editor.target.winfo_rootx(), self.window.editor.source.winfo_rootx())
        with patch("ui.window.SettingsDialog") as dialog:
            settings.invoke()
            dialog.assert_called_once()

    def test_user_undo_prevents_import_layout_from_being_automatically_reapplied(self):
        document = Document(source="First.\n\nSecond.", needs_structure=True,
                            structure_source="First.\n\nSecond.")
        self.window._set_document(document)
        self.window.editor.source.replace("1.0", "end-1c", "First. Second.")
        self.window.editor.get_source()
        self.assertFalse(document.needs_structure)
        self.assertEqual(prepare_request(document).mode, "initial")


class CancellationTests(unittest.TestCase):
    def test_codex_interrupts_active_turn_before_closing_transport(self):
        engine = CodexEngine()
        engine._connect = Mock()
        engine._thread_for = Mock(return_value="thread")
        engine.rpc.request = Mock(return_value={"turn": {"id": "turn"}})
        engine.rpc.close = Mock()
        engine.begin_operation()

        def stop_turn(thread_id, turn_id):
            engine.cancel()
            engine._check_cancelled()

        engine._wait_turn = Mock(side_effect=stop_turn)
        with self.assertRaises(TranslationCancelled):
            engine.translate(prepare_request(Document(source="Hello.")))
        engine.rpc.request.assert_any_call("turn/interrupt", {"threadId": "thread", "turnId": "turn"},
                                           timeout=2, cancellable=False)
        engine.rpc.close.assert_called_once()
        self.assertEqual(engine.last_timing["outcome"], "cancelled")

    def test_deepseek_cancels_stalled_headers_and_stream_and_can_retry(self):
        for phase in ("headers", "stream"):
            with self.subTest(phase=phase):
                entered = threading.Event()
                stopped = threading.Event()
                failures = []
                closed_stream = threading.Event()
                calls = []

                class StalledStream(httpx.AsyncByteStream):
                    async def __aiter__(self):
                        entered.set()
                        await asyncio.sleep(30)
                        yield b""

                    async def aclose(self):
                        closed_stream.set()

                async def handler(request):
                    calls.append(request)
                    if len(calls) > 1:
                        return stream_response('{"patches": []}')
                    if phase == "headers":
                        entered.set()
                        await asyncio.sleep(30)
                    return httpx.Response(200, stream=StalledStream())

                engine = DeepSeekEngine("dummy-key", transport=httpx.MockTransport(handler))
                source = "Three days."
                document = Document(source="Five days.", baseline=source,
                                    segments=[Segment("a", source, "三天。")])
                request = prepare_request(document)
                engine.begin_operation()

                def translate():
                    try:
                        engine.translate(request)
                    except Exception as error:
                        failures.append(error)
                    finally:
                        stopped.set()

                worker = threading.Thread(target=translate, daemon=True)
                worker.start()
                try:
                    self.assertTrue(entered.wait(2), "Request never entered the stalled phase")
                    engine.cancel()
                    self.assertTrue(stopped.wait(2), "Cancellation blocked on network IO")
                    self.assertIsInstance(failures[0], TranslationCancelled)
                    self.assertEqual(engine.last_timing["outcome"], "cancelled")
                    self.assertEqual(document.target, "三天。")
                    if phase == "stream":
                        self.assertTrue(closed_stream.is_set())
                    engine.begin_operation()
                    self.assertEqual(engine.translate(request).patches, ())
                finally:
                    engine.close()
                    worker.join(2)

    def test_codex_response_wait_can_be_cancelled_without_waiting_for_timeout(self):
        rpc = CodexRPC(timeout=90)
        rpc._send = Mock()
        rpc.cancel_event = threading.Event()
        failures = queue.Queue()

        def request():
            try:
                rpc.request("turn/start")
            except Exception as error:
                failures.put(error)

        worker = threading.Thread(target=request, daemon=True)
        worker.start()
        rpc.cancel_event.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(failures.get_nowait(), TranslationCancelled)
        self.assertEqual(rpc._pending, {})


if __name__ == "__main__":
    unittest.main()
