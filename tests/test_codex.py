import json
import unittest
from unittest.mock import Mock

from backend.models import Document, Segment
from backend.providers.base import TranslationEngine, TranslationError
from backend.providers.codex import CodexEngine
from backend.translation import apply_response, prepare_request


class CodexProtocolTests(unittest.TestCase):
    def test_codex_implements_shared_parent(self):
        self.assertIsInstance(CodexEngine(), TranslationEngine)

    def test_deletion_needs_no_codex_connection(self):
        document = Document(source="Delete this.\nKeep this.", baseline="Delete this.\nKeep this.",
                            segments=[Segment("a", "Delete this.\n", "删除。\n"),
                                      Segment("b", "Keep this.", "保留。")])
        document.edit("Keep this.")
        engine = CodexEngine()
        engine.rpc = Mock()
        request = prepare_request(document)
        self.assertTrue(apply_response(document, request, engine.translate(request)))
        self.assertEqual(document.target, "保留。")
        engine.rpc.start.assert_not_called()
        engine.rpc.request.assert_not_called()

    def test_api_authentication_is_rejected(self):
        engine = CodexEngine()
        engine.rpc = Mock()
        engine.rpc.request.return_value = {"account": {"type": "apiKey"}}
        with self.assertRaises(TranslationError):
            engine.connect()
        engine.rpc.close.assert_called_once()

    def test_missing_luna_does_not_fall_back(self):
        engine = CodexEngine()
        engine.rpc = Mock()
        engine.rpc.request.side_effect = [{"account": {"type": "chatgpt"}},
                                          {"data": [{"model": "another-model"}]}]
        with self.assertRaises(TranslationError):
            engine.connect()

    def test_final_message_is_authoritative(self):
        engine = CodexEngine()
        engine.rpc.events.put({"method": "item/agentMessage/delta", "params": {
            "threadId": "t", "turnId": "v", "itemId": "i", "delta": "partial"}})
        engine.rpc.events.put({"method": "item/completed", "params": {
            "threadId": "t", "turnId": "v", "item": {"type": "agentMessage", "id": "i", "text": "final"}}})
        engine.rpc.events.put({"method": "turn/completed", "params": {
            "threadId": "t", "turn": {"id": "v", "status": "completed"}}})
        self.assertEqual(engine._wait_turn("t", "v"), "final")

    def test_failed_turn_does_not_return_partial_translation(self):
        engine = CodexEngine()
        engine.rpc.events.put({"method": "turn/completed", "params": {
            "threadId": "t", "turn": {"id": "v", "status": "failed", "error": {"message": "quota"}}}})
        with self.assertRaisesRegex(TranslationError, "quota"):
            engine._wait_turn("t", "v")

    def test_duplicate_initial_ids_are_rejected(self):
        request = prepare_request(Document(source="A."))
        with self.assertRaises(TranslationError):
            CodexEngine._parse(request, json.dumps({"translations": [
                {"segment_id": "a", "target": "甲"}, {"segment_id": "a", "target": "乙"}]}))

    def test_bad_json_is_rejected(self):
        request = prepare_request(Document(source="A."))
        with self.assertRaises(TranslationError):
            CodexEngine._parse(request, "plain text")


if __name__ == "__main__":
    unittest.main()
