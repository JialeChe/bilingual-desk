from dataclasses import replace
import unittest

from backend.batching import batches
from backend.bilingual import prepare_request, apply_response
from backend.models import Document, ParagraphStart, TranslationResponse
from backend.providers.base import TranslationEngine, TranslationError, TranslationCancelled
from backend.structure import eligible_starts


class FakeEngine(TranslationEngine):
    def __init__(self):
        self.calls = []
        self.fail_at = None
        self.cancel_at = None
        self.last_timing = {}
        self.begin_operation()

    def translate(self, request):
        self.calls.append(request)
        if len(self.calls) == self.fail_at:
            return TranslationResponse(request.document_id, request.revision)  # Truncated/missing units.
        if len(self.calls) == self.cancel_at:
            self.cancel()
        if request.mode == "structure":
            return TranslationResponse(request.document_id, request.revision, paragraphs=tuple(
                ParagraphStart(s.id, "paragraph") for s in request.segments if s.id in eligible_starts(request)))
        kinds = dict(request.unit_kinds)
        return TranslationResponse(request.document_id, request.revision, translations={s.id:
            s.source if kinds.get(s.id) == "code" else ("Translated content." if request.target_language == "en" else "翻译内容。")
            for s in request.segments if s.source.strip()})


class BatchingTests(unittest.TestCase):
    def test_large_documents_over_100k_characters_complete_in_both_directions(self):
        for language in ("zh", "en"):
            source = "\n".join(f"第{i}段内容需要完整翻译，不能遗漏任何细节。" if language == "zh" else
                               f"Paragraph {i} must be translated completely, without omitting any detail."
                               for i in range(5000))
            self.assertGreater(len(source), 100000)
            document = Document(target_text=source) if language == "zh" else Document(source=source)
            request = prepare_request(document)
            engine = FakeEngine()
            response = engine.translate_document(request)
            self.assertEqual(set(response.translations), {s.id for s in request.segments if s.source.strip()})
            self.assertTrue(apply_response(document, request, response))
            self.assertFalse(document.pending)
            self.assertGreater(len(engine.calls), 50)
            self.assertTrue(all(len(c.source_after) <= 1800 and len(c.segments) <= 60 for c in engine.calls))
            self.assertEqual(engine.progress[1], engine.progress[2])

    def test_failed_batch_does_not_commit_and_retry_reuses_validated_batches_with_new_ids(self):
        document = Document(target_text="完整翻译这个段落。\n" * 120)
        request = prepare_request(document)
        before = document.to_dict()
        engine = FakeEngine()
        engine.fail_at = 2
        with self.assertRaisesRegex(TranslationError, "第 2/"):
            engine.translate_document(request)
        self.assertEqual(document.to_dict(), before)
        engine.begin_operation()
        retry = prepare_request(document)
        self.assertNotEqual(request.segments[0].id, retry.segments[0].id)
        response = engine.translate_document(retry)
        self.assertEqual(len(engine.calls), len(batches(request)) + 1)
        self.assertTrue(apply_response(document, retry, response))
        self.assertFalse(document.pending)

    def test_cancellation_keeps_document_and_reuses_only_completed_batches(self):
        document = Document(source="This is a test sentence.\n" * 120)
        request = prepare_request(document)
        before = document.to_dict()
        engine = FakeEngine()
        engine.cancel_at = 2
        with self.assertRaises(TranslationCancelled):
            engine.translate_document(request)
        self.assertEqual(document.to_dict(), before)
        self.assertEqual(len(engine._batch_cache), 1)
        engine.begin_operation()
        self.assertTrue(apply_response(document, request, engine.translate_document(request)))

    def test_modified_input_invalidates_cached_batches(self):
        document = Document(target_text="这是需要翻译的文字。\n" * 120)
        engine = FakeEngine()
        engine.fail_at = 2
        with self.assertRaises(TranslationError):
            engine.translate_document(prepare_request(document))
        document.edit_target(document.target + "结尾。")
        request = prepare_request(document)
        engine.translate_document(request)
        self.assertEqual(len(engine.calls), len(batches(request)) + 2)

    def test_code_fence_crossing_translation_batches_is_preserved(self):
        code = "```text\n" + "保持这些代码字符串不变。\n" * 180 + "```\n"
        document = Document(target_text="正文需要翻译。\n\n" + code + "\n结束。")
        request = prepare_request(document)
        engine = FakeEngine()
        self.assertTrue(apply_response(document, request, engine.translate_document(request)))
        self.assertIn(code, document.source)
        self.assertGreater(len(engine.calls), 3)

    def test_structure_batches_never_split_code_fences(self):
        code = "```text\n" + "这一行代码保持原样。\n" * 200 + "```\n"
        document = Document(target_text="开始。\n\n" + code + "\n结束。", needs_structure=True)
        request = prepare_request(document)
        self.assertEqual(request.mode, "structure")
        chunks = batches(request)
        self.assertTrue(any(code in chunk.source_after for chunk in chunks))
        engine = FakeEngine()
        self.assertTrue(apply_response(document, request, engine.translate_document(request)))
        self.assertIn(code, document.target)
        self.assertEqual(prepare_request(document).mode, "initial")


if __name__ == "__main__":
    unittest.main()
