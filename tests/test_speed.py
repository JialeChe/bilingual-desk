import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock

from backend.models import Document, Segment
from backend.providers.base import TranslationError
from backend.providers.codex import CodexEngine
from backend.translation import prepare_request


class SpeedTests(unittest.TestCase):
    def initial(self):
        return prepare_request(Document(source="Private English sample."))

    def revision(self):
        document = Document(source="Three days.", baseline="Three days.",
                            segments=[Segment("a", "Three days.", "三天。")])
        document.edit("Five days.")
        return prepare_request(document)

    def engine(self, request, fast=False, timings_path=None, fail=False):
        engine = CodexEngine(fast_updates=fast, timings_path=timings_path)
        engine._connect = Mock()
        engine._fast_tier = "priority"
        engine.rpc = Mock()
        engine.rpc.events = queue.Queue()
        engine.rpc.timeout = 1
        if request.mode == "initial":
            raw = json.dumps({"translations": [{"segment_id": segment.id, "target": "测试译文。"}
                             for segment in request.segments if segment.source.strip()]})
        elif request.mode == "sentence":
            raw = json.dumps({"translations": [{"segment_id": s.id, "target": "五天。"}
                              for s in request.sentences if s.needs_translation]})
        else:
            raw = json.dumps({"patches": [{"segment_id": "a", "before": "三", "after": "五",
                                         "left": "", "right": "天。"}]})

        def respond(method, params):
            if method == "thread/start":
                return {"thread": {"id": "t"}}
            if method == "turn/start":
                if fail:
                    engine.rpc.events.put({"method": "error", "params": {
                        "threadId": "t", "error": {"message": "Reconnecting... 1/5"}}})
                else:
                    engine.rpc.events.put({"method": "item/agentMessage/delta", "params": {
                        "threadId": "t", "turnId": "v", "itemId": "i", "delta": raw}})
                    engine.rpc.events.put({"method": "item/completed", "params": {
                        "threadId": "t", "turnId": "v", "item": {
                            "type": "agentMessage", "id": "i", "text": raw}}})
                engine.rpc.events.put({"method": "turn/completed", "params": {
                    "threadId": "t", "turn": {"id": "v", "status": "failed" if fail else "completed",
                                                   "error": {"message": "network"} if fail else None}}})
                return {"turn": {"id": "v"}}
            self.fail(f"Unexpected RPC: {method}")

        engine.rpc.request.side_effect = respond
        return engine

    def turn_params(self, engine):
        return next(c.args[1] for c in engine.rpc.request.call_args_list if c.args[0] == "turn/start")

    def test_discovery_uses_wire_tier_id_not_fast_ui_name(self):
        engine = CodexEngine(fast_updates=True)
        engine.rpc = Mock()
        engine.rpc.request.side_effect = [
            {"account": {"type": "chatgpt"}},
            {"data": [{"model": "gpt-6-luna", "serviceTiers": [
                {"id": "priority", "name": "Fast", "description": "1.5x speed"}]}]}]
        self.assertIn("后续 Fast", engine.connect())
        self.assertEqual(engine._fast_tier, "priority")

    def test_fast_is_opt_in_and_standard_turn_does_not_inherit_fast(self):
        request = self.revision()
        engine = self.engine(request)
        engine.translate(request)
        self.assertEqual(self.turn_params(engine)["serviceTierForTurn"], "default")
        self.assertEqual(self.turn_params(engine)["effort"], "low")

    def test_fast_updates_keep_initial_translation_standard(self):
        request = self.initial()
        engine = self.engine(request, fast=True)
        engine.translate(request)
        self.assertEqual(self.turn_params(engine)["serviceTierForTurn"], "default")

    def test_enabled_fast_revision_sends_catalog_tier(self):
        request = self.revision()
        engine = self.engine(request, fast=True)
        response = engine.translate(request)
        self.assertEqual(self.turn_params(engine)["serviceTierForTurn"], "priority")
        self.assertEqual(response.patches[0].after, "五")

    def test_fast_sentence_updates_use_complete_translation_schema(self):
        from backend.bilingual import prepare_request, apply_response
        document = Document(source="Five days.", baseline="Three days.",
                            segments=[Segment("a", "Three days.", "三天。")])
        request = prepare_request(document)
        engine = self.engine(request, fast=True)
        response = engine.translate(request)
        self.assertEqual(self.turn_params(engine)["serviceTierForTurn"], "priority")
        self.assertEqual(set(self.turn_params(engine)["outputSchema"]["properties"]), {"translations"})
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, "五天。")

    def test_unavailable_fast_does_not_silently_switch_model_or_send_turn(self):
        request = self.revision()
        engine = self.engine(request, fast=True)
        engine._fast_tier = None
        with self.assertRaisesRegex(TranslationError, "未提供 Fast"):
            engine.translate(request)
        engine.rpc.request.assert_not_called()
        self.assertEqual(engine.last_timing["outcome"], "error")

    def test_timings_record_first_output_and_never_log_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "timings.jsonl"
            request = self.initial()
            engine = self.engine(request, timings_path=path)
            engine.translate(request)
            contents = path.read_text(encoding="utf-8")
            self.assertNotIn("Private", contents)
            self.assertNotIn("测试译文", contents)
            self.assertNotIn(request.document_id, contents)
            timing = json.loads(contents)
            self.assertEqual(timing["outcome"], "completed")
            self.assertIsNotNone(timing["first_output_seconds"])
            self.assertGreaterEqual(timing["total_seconds"], timing["generation_seconds"])
            self.assertGreater(timing["prompt_characters"], 0)

    def test_reconnections_and_failures_are_measured_without_partial_results(self):
        request = self.revision()
        engine = self.engine(request, fail=True)
        with self.assertRaisesRegex(TranslationError, "network"):
            engine.translate(request)
        self.assertEqual(engine.last_timing["reconnection_events"], 1)
        self.assertEqual(engine.last_timing["outcome"], "error")
        self.assertIsNone(engine.last_timing["first_output_seconds"])

    def test_unwritable_diagnostics_dont_fail_valid_translation(self):
        with tempfile.TemporaryDirectory() as folder:
            request = self.revision()
            engine = self.engine(request, timings_path=Path(folder))
            self.assertEqual(engine.translate(request).patches[0].after, "五")


if __name__ == "__main__":
    unittest.main()
