import json
import unittest

from backend.language import untranslated_ids
from backend.models import Document, Segment, TargetPatch, TranslationResponse
from backend.providers.protocol import build_prompt
from backend.translation import PatchError, apply_response, prepare_request


class LanguageTests(unittest.TestCase):
    def test_multilingual_plain_prose_cannot_be_committed_as_an_unchanged_copy(self):
        for source in ("I love you,and i wanna fuck you", "Bonjour, je suis heureux.",
                       "こんにちは", "안녕하세요", "Я люблю тебя", "مرحبا بالعالم"):
            with self.subTest(source=source):
                document = Document(source=source)
                request = prepare_request(document)
                response = TranslationResponse(document.id, document.revision,
                          translations={s.id: s.source for s in request.segments if s.source.strip()})
                before = document.to_dict()
                with self.assertRaisesRegex(PatchError, "原样返回了外语句子"):
                    apply_response(document, request, response)
                self.assertEqual(document.to_dict(), before)

    def test_original_chinese_is_valid_and_stays_unchanged(self):
        source = "什么是最好的？\n我爱你"
        document = Document(source=source)
        request = prepare_request(document)
        response = TranslationResponse(document.id, document.revision,
                    translations={s.id: s.source for s in request.segments if s.source.strip()})
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, source)

    def test_names_code_and_urls_are_not_mistaken_for_untranslated_prose(self):
        for source in ("Python", "New York", "API", "https://example.com", "`hello world`",
                       "```python\nprint('hello world')\n# a comment\n```\n"):
            with self.subTest(source=source):
                self.assertEqual(untranslated_ids((Segment("a", source, source),)), ())

    def test_chinese_with_necessary_foreign_names_and_abbreviations_is_preserved(self):
        for source in ("我使用 Python API。", "阅读 New York Times。", "使用 python。"):
            with self.subTest(source=source):
                self.assertEqual(untranslated_ids((Segment("a", source, source),)), ())

    def test_saved_copied_sentence_can_be_repaired_without_source_edits(self):
        chinese = Segment("a", "什么是最好的？\n我爱你\n", "什么是最好的？\n我爱你\n")
        bad = Segment("b", "I love you,and i wanna fuck you", "I love you,and i wanna fuck you")
        source = chinese.source + bad.source
        document = Document(source=source, baseline=source, segments=[chinese, bad])
        self.assertFalse(document.pending)
        request = prepare_request(document)
        self.assertIsNotNone(request)
        self.assertEqual(request.mode, "revise")
        self.assertEqual(request.repair_ids, ("b",))
        self.assertEqual(request.affected_ids, ("b",))
        self.assertEqual(request.edits, ())
        snapshot = json.loads(build_prompt(request).split("\n", 1)[1])
        unit = next(u for u in snapshot["units"] if u["segment_id"] == "b")
        self.assertTrue(unit["editable"])
        self.assertTrue(unit["repair_translation"])
        response = TranslationResponse(document.id, document.revision,
                       patches=(TargetPatch("b", bad.target, "我爱你，我想和你做爱。"),))
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.segments[0], chinese)
        self.assertEqual(document.source, source)
        self.assertEqual(document.target, chinese.target + "我爱你，我想和你做爱。")
        self.assertIsNone(prepare_request(document))

    def test_copied_foreign_insertion_is_rejected_even_in_a_mixed_language_unit(self):
        document = Document(source="我爱你\n", baseline="我爱你\n",
                            segments=[Segment("a", "我爱你\n", "我爱你\n")])
        document.edit("我爱你\nI love you")
        request = prepare_request(document)
        before = document.to_dict()
        response = TranslationResponse(document.id, document.revision,
                         patches=(TargetPatch("a", "", "I love you", left="我爱你\n"),))
        with self.assertRaisesRegex(PatchError, "原样返回了外语句子"):
            apply_response(document, request, response)
        self.assertEqual(document.to_dict(), before)


if __name__ == "__main__":
    unittest.main()
