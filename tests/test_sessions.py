import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from backend.models import Document
from backend.providers.base import TranslationError
from backend.providers.codex import CodexEngine, SessionBusyError
from backend.providers.sessions import DocumentSessions
from backend.translation import prepare_request


class SessionTests(unittest.TestCase):
    def test_same_document_reuses_thread_across_turns_and_switches(self):
        engine = CodexEngine()
        engine._connect = Mock()
        engine.rpc = Mock()
        engine.rpc.events.get_nowait.side_effect = __import__("queue").Empty
        starts = []

        def respond(method, params):
            if method == "thread/start":
                starts.append(params)
                return {"thread": {"id": f"thread-{len(starts)}"}}
            if method == "turn/start":
                self.assertEqual(params["effort"], "low")
                return {"turn": {"id": "turn"}}
            self.fail(f"Unexpected method: {method}")

        engine.rpc.request.side_effect = respond
        doc1, doc2 = Document(source="One."), Document(source="Two.")
        for doc in (doc1, doc1, doc2, doc1):
            request = prepare_request(doc)
            engine._wait_turn = Mock(return_value=json.dumps({"translations": [
                {"segment_id": request.segments[0].id, "target": "译文。"}]}))
            engine.translate(request)
        self.assertEqual(len(starts), 2)
        self.assertTrue(all(p["ephemeral"] is False for p in starts))
        turns = [c.args[1]["threadId"] for c in engine.rpc.request.call_args_list
                 if c.args[0] == "turn/start"]
        self.assertEqual(turns, ["thread-1", "thread-1", "thread-2", "thread-1"])

    def test_new_process_restores_persisted_session(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sessions.json"
            first = CodexEngine(sessions_path=path)
            first.rpc = Mock()
            first.rpc.request.return_value = {"thread": {"id": "persisted"}}
            self.assertEqual(first._thread_for("doc"), "persisted")
            first.close()
            second = CodexEngine(sessions_path=path)
            second.rpc = Mock()
            second.rpc.request.return_value = {"thread": {"id": "persisted"}}
            self.assertEqual(second._thread_for("doc"), "persisted")
            self.assertEqual(second._thread_for("doc"), "persisted")
            second.rpc.request.assert_called_once()
            self.assertEqual(second.rpc.request.call_args.args[0], "thread/resume")

    def test_missing_thread_can_be_recreated(self):
        engine = CodexEngine()
        engine.sessions.bind("doc", "missing")
        engine.rpc = Mock()
        engine.rpc.request.side_effect = [TranslationError("no rollout found"),
                                          {"thread": {"id": "replacement"}}]
        self.assertEqual(engine._thread_for("doc"), "replacement")
        self.assertEqual(engine.session_for("doc"), "replacement")

    def test_active_writer_preserves_binding_and_requires_explicit_recreation(self):
        engine = CodexEngine()
        engine.sessions.bind("doc", "occupied")
        engine.rpc = Mock()
        engine.rpc.request.side_effect = TranslationError(
            "thread occupied already has an active writer")
        with self.assertRaisesRegex(SessionBusyError, "其他进程占用"):
            engine._thread_for("doc")
        engine.rpc.request.assert_called_once()
        self.assertEqual(engine.session_for("doc"), "occupied")
        self.assertNotIn("doc", engine._loaded_threads)

    def test_explicit_recreation_stores_new_session_without_resuming_or_deleting_old(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sessions.json"
            engine = CodexEngine(sessions_path=path)
            engine.sessions.bind("doc", "occupied")
            engine._connect = Mock()
            engine.rpc = Mock()
            engine.rpc.request.return_value = {"thread": {"id": "new-thread"}}
            self.assertEqual(engine.recreate_session("doc"), "new-thread")
            engine.rpc.request.assert_called_once()
            self.assertEqual(engine.rpc.request.call_args.args[0], "thread/start")
            self.assertEqual(engine._thread_for("doc"), "new-thread")
            self.assertEqual(DocumentSessions(path).get("doc"), "new-thread")

    def test_failed_recreation_preserves_original_binding(self):
        engine = CodexEngine()
        engine.sessions.bind("doc", "occupied")
        engine._connect = Mock()
        engine.rpc = Mock()
        engine.rpc.request.side_effect = TranslationError("quota")
        with self.assertRaisesRegex(TranslationError, "quota"):
            engine.recreate_session("doc")
        self.assertEqual(engine.session_for("doc"), "occupied")

    def test_other_resume_errors_preserve_binding_and_do_not_create_thread(self):
        for message in ("quota exceeded", "authentication failed", "timeout"):
            engine = CodexEngine()
            engine.sessions.bind("doc", "original")
            engine.rpc = Mock()
            engine.rpc.request.side_effect = TranslationError(message)
            with self.assertRaises(TranslationError):
                engine._thread_for("doc")
            engine.rpc.request.assert_called_once()
            self.assertEqual(engine.session_for("doc"), "original")

    def test_failed_turn_keeps_document_binding(self):
        engine = CodexEngine()
        engine._connect = Mock()
        engine.sessions.bind("doc", "original")
        engine._loaded_threads["doc"] = "original"
        engine.rpc = Mock()
        engine.rpc.events.get_nowait.side_effect = __import__("queue").Empty
        engine.rpc.request.side_effect = TranslationError("quota")
        with self.assertRaises(TranslationError):
            engine.translate(prepare_request(Document(id="doc", source="Hello.")))
        self.assertEqual(engine.session_for("doc"), "original")
        self.assertEqual(engine._loaded_threads, {})
        engine.rpc.close.assert_called_once()

    def test_corrupt_session_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sessions.json"
            path.write_text("bad json", encoding="utf-8")
            with self.assertRaises(TranslationError):
                DocumentSessions(path)
            self.assertEqual(path.read_text(), "bad json")


if __name__ == "__main__":
    unittest.main()
