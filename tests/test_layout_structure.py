import json
import os
from pathlib import Path
import re
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import httpx

from backend.jobs import JobResult
from backend.models import Document, ParagraphStart, Segment, TargetPatch, TranslationResponse
from backend.providers.base import TranslationError
from backend.providers.deepseek import DeepSeekEngine
from backend.providers.codex import CodexEngine
from backend.providers.protocol import build_prompt, local_response, output_schema, parse_response
from backend.storage import load_document, save_document
from backend.translation import PatchError, apply_response, prepare_request
from tests.test_connection_flow import StubEngine
from tests.test_deepseek import stream_response
from ui.window import MainWindow


def snapshot(request):
    return json.loads(build_prompt(request).split("\n", 1)[1])


class LayoutTests(unittest.TestCase):
    def test_initial_request_excludes_blank_units_and_boundary_whitespace(self):
        source = "\t Hello.  \r\n\r\n  World.\t "
        document = Document(source=source)
        request = prepare_request(document)
        units = snapshot(request)["units"]
        self.assertEqual([u["source"] for u in units], ["Hello.", "World."])
        response = TranslationResponse(document.id, document.revision, translations={
            u["segment_id"]: " \n你好。\t " if u["source"] == "Hello." else "世界。" for u in units})
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.source, source)
        self.assertEqual(document.target, "\t 你好。  \r\n\r\n  世界。\t ")

    def test_spaces_and_paragraph_newlines_need_no_api_key_or_http_request(self):
        source = "Hello. World."
        document = Document(source=source, baseline=source, segments=[
            Segment("a", "Hello. ", "你好。 "), Segment("b", "World.", "世界。")])
        document.edit("Hello.\n\nWorld.")
        request = prepare_request(document)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            engine = DeepSeekEngine(transport=httpx.MockTransport(lambda _: self.fail("HTTP called")))
        try:
            self.assertTrue(apply_response(document, request, engine.translate(request)))
            self.assertEqual(document.target, "你好。\n\n世界。")
            self.assertEqual(engine.last_timing["outcome"], "local")
            self.assertFalse(document.pending)
        finally:
            engine.close()

    def test_internal_word_spaces_preserve_translation_without_request(self):
        document = Document(source="We finish in three days.", baseline="We finish in three days.",
                            segments=[Segment("a", "We finish in three days.", "我们三天内完成。")])
        document.edit("We  finish\tin three days.")
        request = prepare_request(document)
        self.assertTrue(apply_response(document, request, local_response(request)))
        self.assertEqual(document.target, "我们三天内完成。")

    def test_layout_and_content_changes_commit_together_without_patching_layout_unit(self):
        source = "Hello. Three days."
        document = Document(source=source, baseline=source, segments=[
            Segment("a", "Hello. ", "你好。 "), Segment("b", "Three days.", "三天。")])
        document.edit("Hello.\n\nFive days.")
        request = prepare_request(document)
        units = {u["segment_id"]: u for u in snapshot(request)["units"]}
        self.assertFalse(units["a"]["editable"])
        self.assertTrue(units["a"]["layout_only"])
        self.assertTrue(units["b"]["editable"])
        before = document.to_dict()
        with self.assertRaises(PatchError):
            apply_response(document, request, TranslationResponse(document.id, document.revision,
                           patches=(TargetPatch("a", "你好", "欢迎"),)))
        self.assertEqual(document.to_dict(), before)
        response = TranslationResponse(document.id, document.revision,
                                       patches=(TargetPatch("b", "三", "五"),))
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, "你好。\n\n五天。")

    def test_identical_chinese_and_code_layout_is_copied_locally(self):
        for before, after in (("我 爱你。", "我  爱你。"), ("`x = 1`", "`x  = 1`")):
            document = Document(source=before, baseline=before, segments=[Segment("a", before, before)])
            document.edit(after)
            request = prepare_request(document)
            self.assertIsNotNone(local_response(request))
            self.assertTrue(apply_response(document, request, local_response(request)))
            self.assertEqual(document.target, after)


class StructureTests(unittest.TestCase):
    def response(self, request, names):
        rows = []
        for text, kind in names:
            segment = next(s for s in request.segments if s.source.strip() == text)
            rows.append(ParagraphStart(segment.id, kind))
        return TranslationResponse(request.document_id, request.revision, paragraphs=tuple(rows))

    def test_structure_precedes_translation_and_adds_only_newlines(self):
        source = "  Title\r\nFirst sentence.  Second sentence.\r\n\r\nLast.  "
        document = Document(source=source, needs_structure=True)
        request = prepare_request(document)
        self.assertEqual(request.mode, "structure")
        self.assertIsNone(local_response(request))
        self.assertEqual(output_schema("structure")["required"], ["paragraphs"])
        response = self.response(request, [("Title", "heading"), ("First sentence.", "paragraph"),
                                           ("Last.", "paragraph")])
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.source, "  Title\r\n\r\nFirst sentence.  Second sentence.\r\n\r\nLast.  ")
        self.assertEqual(re.sub(r"[\r\n]", "", document.source), re.sub(r"[\r\n]", "", source))
        self.assertEqual(document.target, "")
        self.assertEqual(prepare_request(document).mode, "initial")

    def test_bad_boundary_order_duplicates_and_missing_first_are_atomic(self):
        for names in ([('Second.', 'paragraph')], [('First.', 'paragraph'), ('First.', 'paragraph')],
                      [('First.', 'paragraph'), ('Third.', 'paragraph'), ('Second.', 'paragraph')]):
            document = Document(source="First. Second. Third.", needs_structure=True)
            request = prepare_request(document)
            before = document.to_dict()
            with self.assertRaises(TranslationError):
                apply_response(document, request, self.response(request, names))
            self.assertEqual(document.to_dict(), before)

    def test_code_block_cannot_be_split_into_paragraphs(self):
        document = Document(source="```python\n# comment\nx = 1\n```\n", needs_structure=True)
        request = prepare_request(document)
        before = document.to_dict()
        with self.assertRaisesRegex(TranslationError, "代码块"):
            apply_response(document, request, self.response(request, [("```python", "code"), ("# comment", "paragraph")]))
        self.assertEqual(document.to_dict(), before)

    def test_mid_sentence_candidates_cannot_become_paragraph_starts(self):
        document = Document(source="First clause, second clause.", needs_structure=True)
        request = prepare_request(document)
        before = document.to_dict()
        with self.assertRaisesRegex(TranslationError, "句子中间"):
            apply_response(document, request, self.response(request, [
                ("First clause,", "paragraph"), ("second clause.", "paragraph")]))
        self.assertEqual(document.to_dict(), before)

    def test_codex_structure_uses_document_thread_and_structure_schema(self):
        document = Document(source="Title\nFirst.", needs_structure=True)
        request = prepare_request(document)
        response = self.response(request, [("Title", "heading"), ("First.", "paragraph")])
        engine = CodexEngine(fast_updates=True)
        engine._connect = Mock()
        engine._thread_for = Mock(return_value="document-thread")
        engine.rpc.request = Mock(return_value={"turn": {"id": "structure-turn"}})
        engine._wait_turn = Mock(return_value=json.dumps({"paragraphs": [
            {"segment_id": p.segment_id, "kind": p.kind} for p in response.paragraphs]}))
        actual = engine.translate(request)
        engine._thread_for.assert_called_once_with(document.id)
        params = engine.rpc.request.call_args.args[1]
        self.assertEqual(params["outputSchema"]["required"], ["paragraphs"])
        self.assertEqual(params["serviceTierForTurn"], "default")
        self.assertEqual(actual.paragraphs, response.paragraphs)

    def test_stale_structure_never_overwrites_user_edits(self):
        document = Document(source="First. Second.", needs_structure=True)
        request = prepare_request(document)
        response = self.response(request, [("First.", "paragraph"), ("Second.", "paragraph")])
        document.edit("New text. First. Second.")
        before = document.to_dict()
        self.assertFalse(apply_response(document, request, response))
        self.assertEqual(document.to_dict(), before)
        self.assertEqual(prepare_request(document).mode, "structure")

    def test_initialized_draft_skips_reinitialization_on_retry_or_restart(self):
        document = Document(source="First. Second.", needs_structure=True)
        request = prepare_request(document)
        apply_response(document, request, self.response(request, [("First.", "paragraph"), ("Second.", "paragraph")]))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "draft.json"
            save_document(path, document)
            recovered = load_document(path)
        self.assertEqual(recovered.to_dict(), document.to_dict())
        self.assertEqual(prepare_request(recovered).mode, "initial")
        recovered.edit(recovered.source + " Third.")
        self.assertEqual(prepare_request(recovered).mode, "structure")

    def test_deepseek_uses_structure_schema_and_keeps_thinking_disabled(self):
        document = Document(source="Title\nFirst. Second.", needs_structure=True)
        request = prepare_request(document)

        def handler(http_request):
            body = json.loads(http_request.content)
            self.assertEqual(body["thinking"], {"type": "disabled"})
            units = json.loads(body["messages"][1]["content"].split("\n", 1)[1])["units"]
            self.assertEqual(units[1]["line_breaks_before"], 1)
            raw = {"paragraphs": [{"segment_id": units[0]["segment_id"], "kind": "heading"},
                                   {"segment_id": units[1]["segment_id"], "kind": "paragraph"}]}
            return stream_response(json.dumps(raw))

        engine = DeepSeekEngine("synthetic-key", transport=httpx.MockTransport(handler))
        try:
            self.assertTrue(apply_response(document, request, engine.translate(request)))
            self.assertEqual(document.source, "Title\n\nFirst. Second.")
        finally:
            engine.close()

    def test_structure_parser_rejects_text_rewrites_and_wrong_fields(self):
        request = prepare_request(Document(source="First.", needs_structure=True))
        for raw in ('{"paragraphs": [] , "source": "Changed."}',
                    '{"paragraphs": [{"segment_id": "x", "kind": "unknown"}]}',
                    '{"translations": []}'):
            with self.assertRaises(TranslationError):
                parse_response(request, raw, "test")


class ImportFlowTests(unittest.TestCase):
    def test_manual_import_initializes_then_translates_even_with_auto_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source_file = directory / "new.md"
            source_file.write_bytes(b"Title\r\nHello. World.")
            root = tk.Tk()
            root.withdraw()
            with patch.object(MainWindow, "_new_engine", return_value=StubEngine()):
                window = MainWindow(root, directory, auto_connect=False)
            root.update()
            try:
                window.auto.set(False)
                window.settings["organize_imports"] = True
                window.jobs.submit = Mock()
                with patch("ui.window.filedialog.askopenfilename", return_value=str(source_file)):
                    window.open()
                self.assertTrue(window.document.needs_structure)
                self.assertEqual(window.document.source, "Title\r\nHello. World.")
                window.translate()
                request = window.jobs.submit.call_args.args[1]
                self.assertEqual(request.mode, "structure")
                starts = tuple(ParagraphStart(s.id, "heading" if s.source.strip() == "Title" else "paragraph")
                               for s in request.segments if s.source.strip() in ("Title", "Hello."))
                window.jobs.results.put(JobResult("translation", request, TranslationResponse(
                    request.document_id, request.revision, paragraphs=starts)))
                root.after_cancel(window._poll_timer)
                window._poll()
                initial = window.jobs.submit.call_args.args[1]
                self.assertEqual(initial.mode, "initial")
                self.assertTrue(window.busy)
                self.assertEqual(window.editor.get_source(), "Title\r\n\r\nHello. World.")
                targets = {"Title": "标题", "Hello.": "你好。", "World.": "世界。"}
                response = TranslationResponse(initial.document_id, initial.revision, translations={
                    s.id: targets[s.source.strip()] for s in initial.segments if s.source.strip()})
                window.jobs.results.put(JobResult("translation", initial, response))
                root.after_cancel(window._poll_timer)
                window._poll()
                self.assertFalse(window.document.needs_structure)
                self.assertFalse(window.document.pending)
                self.assertEqual(window.document.target, "标题\r\n\r\n你好。 世界。")
                self.assertEqual(window.jobs.submit.call_count, 2)
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
