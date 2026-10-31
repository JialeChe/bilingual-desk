import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from backend.models import Document, Segment
from backend.providers.base import TranslationEngine, TranslationError
from backend.providers.deepseek import DeepSeekEngine
from backend.translation import PatchError, apply_response, prepare_request


def stream_response(text, finish="stop", done=True):
    events = [": keep-alive\n\n"]
    for piece in (text[:len(text)//2], text[len(text)//2:]):
        events.append("data: " + json.dumps({"choices": [{"delta": {"content": piece},
                          "finish_reason": None}]}, ensure_ascii=False) + "\n\n")
    events.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]}) + "\n\n")
    if done:
        events.append("data: [DONE]\n\n")
    return httpx.Response(200, text="".join(events), headers={"content-type": "text/event-stream"})


def pending_document():
    document = Document(source="Three days. Budget unchanged.", baseline="Three days. Budget unchanged.",
                        segments=[Segment("a", "Three days. ", "三天。 "),
                                  Segment("b", "Budget unchanged.", "预算不变。")])
    document.edit(document.source.replace("Three", "Five"))
    return document


class DeepSeekTests(unittest.TestCase):
    def engine(self, answer, **options):
        requests = []

        def handler(request):
            requests.append(request)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/chat/completions")
            return answer(request) if callable(answer) else answer

        engine = DeepSeekEngine("dummy-test-key", transport=httpx.MockTransport(handler), **options)
        self.addCleanup(engine.close)
        return engine, requests

    def test_initial_and_revision_use_shared_parent_and_only_change_requested_chinese(self):
        from backend.bilingual import prepare_request, apply_response
        def answer(request):
            body = json.loads(request.content)
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertEqual(body["reasoning_effort"], "none")
            self.assertEqual(body["model"], "deepseek-flash")
            self.assertEqual(body["response_format"], {"type": "json_object"})
            self.assertNotIn("tools", body)
            snapshot = json.loads(body["messages"][1]["content"].split("\n", 1)[1])
            if snapshot["mode"] == "initial":
                result = {"translations": [{"segment_id": unit["segment_id"],
                    "target": "三天。 " if "Three" in unit["source"] else "预算不变。"}
                    for unit in snapshot["units"]]}
            else:
                self.assertEqual(snapshot["mode"], "sentence")
                self.assertEqual(len(snapshot["units"]), 1)
                affected = snapshot["units"][0]
                self.assertIn("Five", affected["source_after"])
                result = {"translations": [{"segment_id": affected["segment_id"], "target": "五天。"}]}
            return stream_response(json.dumps(result, ensure_ascii=False))

        engine, requests = self.engine(answer)
        self.assertIsInstance(engine, TranslationEngine)
        document = Document(source="Three days. Budget unchanged.")
        request = prepare_request(document)
        self.assertTrue(apply_response(document, request, engine.translate(request)))
        unchanged = document.segments[-1].target
        document.edit(document.source.replace("Three", "Five"))
        request = prepare_request(document)
        self.assertTrue(apply_response(document, request, engine.translate(request)))
        self.assertEqual(document.target, "五天。 预算不变。")
        self.assertEqual(document.segments[-1].target, unchanged)
        self.assertEqual(len(requests), 2)
        self.assertIsNotNone(engine.last_timing["first_output_seconds"])

    def test_revision_excludes_distant_unaffected_context(self):
        from backend.bilingual import prepare_request
        units = [Segment(str(i), f"Sentence {i}. ", f"第{i}句。 ") for i in range(10)]
        source = "".join(unit.source for unit in units)
        document = Document(source=source, baseline=source, segments=units)
        document.edit(source.replace("Sentence 0", "Edited 0"))
        engine, requests = self.engine(stream_response('{"translations": []}'))
        engine.translate(prepare_request(document))
        body = json.loads(requests[-1].content)
        self.assertNotIn("Sentence 9", body["messages"][1]["content"])
        self.assertNotIn("第9句", body["messages"][1]["content"])
        self.assertNotIn("source_after", json.loads(body["messages"][1]["content"].split("\n", 1)[1]))

    def test_multilingual_chinese_output_uses_shared_prompt_and_preserves_tone(self):
        def answer(request):
            body = json.loads(request.content)
            system = body["messages"][0]["content"]
            self.assertIn("source_language 和 target_language 决定方向", system)
            self.assertIn("成人表达", system)
            snapshot = json.loads(body["messages"][1]["content"].split("\n", 1)[1])
            self.assertEqual(snapshot["target_language"], "zh")
            translations = {"我爱你": "我爱你", "こんにちは": "你好",
                            "I love you,and i wanna fuck you": "我爱你，我想和你做爱。"}
            result = {"translations": [{"segment_id": u["segment_id"], "target": translations[u["source"]]}
                                       for u in snapshot["units"]]}
            return stream_response(json.dumps(result, ensure_ascii=False))

        engine, _ = self.engine(answer)
        document = Document(source="我爱你\nこんにちは\nI love you,and i wanna fuck you")
        request = prepare_request(document)
        self.assertTrue(apply_response(document, request, engine.translate(request)))
        self.assertEqual(document.target, "我爱你\n你好\n我爱你，我想和你做爱。")

    def test_incomplete_and_truncated_streams_do_not_modify_document(self):
        for finish, done in (("stop", False), ("length", True), ("tool_calls", True)):
            document = pending_document()
            before = document.to_dict()
            engine, _ = self.engine(stream_response('{"patches": []}', finish, done))
            with self.assertRaises(TranslationError):
                engine.translate(prepare_request(document))
            self.assertEqual(document.to_dict(), before)

    def test_bad_json_and_unexpected_fields_are_rejected(self):
        for content in ("not JSON", '{"patches": [], "translations": []}', '{"patches": null}',
                        '{"patches": [{"segment_id": "a", "before": 3}]}'):
            engine, _ = self.engine(stream_response(content))
            with self.assertRaises(TranslationError):
                engine.translate(prepare_request(pending_document()))

    def test_affected_sentence_can_be_rewritten_through_api(self):
        document = pending_document()
        content = json.dumps({"patches": [{"segment_id": "a", "before": "三天。 ",
                              "after": "五天即可完成。", "left": "", "right": ""}]})
        engine, _ = self.engine(stream_response(content))
        request = prepare_request(document)
        response = engine.translate(request)
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, "五天即可完成。预算不变。")

    def test_deletion_needs_no_key_or_http_request(self):
        document = pending_document()
        document.edit("Budget unchanged.")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            engine = DeepSeekEngine(transport=httpx.MockTransport(lambda request: self.fail("HTTP called")))
        self.addCleanup(engine.close)
        request = prepare_request(document)
        self.assertTrue(apply_response(document, request, engine.translate(request)))
        self.assertEqual(document.target, "预算不变。")
        self.assertEqual(engine.last_timing["outcome"], "local")
        self.assertFalse(engine.requires_connection)

    def test_status_errors_never_echo_server_body_or_key(self):
        for code, message in ((401, "API Key 无效"), (402, "余额不足"), (429, "请求限流")):
            engine, _ = self.engine(httpx.Response(code, json={"error": "dummy-test-key private text"}))
            with self.assertRaisesRegex(TranslationError, message) as caught:
                engine.translate(prepare_request(pending_document()))
            self.assertNotIn("dummy-test-key", str(caught.exception))
            self.assertNotIn("private text", str(caught.exception))

    def test_redirect_is_not_followed_with_credentials(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(302, headers={"location": "https://other.invalid/"})

        engine = DeepSeekEngine("dummy-test-key", transport=httpx.MockTransport(handler))
        self.addCleanup(engine.close)
        with self.assertRaises(TranslationError):
            engine.translate(prepare_request(pending_document()))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.host, "api.deepseek.com")

    def test_timeout_preserves_baseline(self):
        def timeout(request):
            raise httpx.ReadTimeout("sensitive internal message", request=request)

        engine, _ = self.engine(timeout)
        document = pending_document()
        before = document.to_dict()
        with self.assertRaisesRegex(TranslationError, "超时"):
            engine.translate(prepare_request(document))
        self.assertEqual(document.to_dict(), before)
        self.assertEqual(engine.last_timing["outcome"], "error")

    def test_missing_key_does_not_make_http_request(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            engine = DeepSeekEngine(transport=httpx.MockTransport(lambda request: self.fail("HTTP called")))
        self.addCleanup(engine.close)
        with self.assertRaisesRegex(TranslationError, "设置中填写"):
            engine.translate(prepare_request(pending_document()))

    def test_environment_key_is_supported_without_settings_storage(self):
        calls = []

        def handler(request):
            calls.append(request)
            self.assertEqual(request.headers["Authorization"], "Bearer dummy-env-key")
            return stream_response('{"patches": []}')

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "dummy-env-key"}):
            engine = DeepSeekEngine(transport=httpx.MockTransport(handler))
        self.addCleanup(engine.close)
        engine.translate(prepare_request(pending_document()))
        self.assertEqual(len(calls), 1)
        self.assertIn("关闭思考", engine.connect())

    def test_api_has_no_connection_handshake(self):
        engine = DeepSeekEngine("dummy-test-key", transport=httpx.MockTransport(
            lambda request: self.fail("HTTP called before translation")))
        self.addCleanup(engine.close)
        self.assertFalse(engine.requires_connection)
        self.assertEqual(engine.connect(), engine.connection_label())

    def test_timings_do_not_contain_text_or_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "timings.jsonl"
            engine, _ = self.engine(stream_response('{"patches": []}'), timings_path=path)
            engine.translate(prepare_request(pending_document()))
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("dummy-test-key", text)
            self.assertNotIn("Three", text)
            self.assertNotIn("预算", text)
            self.assertEqual(json.loads(text)["thinking"], "disabled")


if __name__ == "__main__":
    unittest.main()
