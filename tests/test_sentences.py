import json
import unittest

from backend.bilingual import prepare_request, apply_response, resolve_conflicts, ConflictingEdits
from backend.models import Document, Segment, TranslationResponse
from backend.providers.protocol import build_prompt, parse_response, output_schema, local_response
from backend.sentences import sentence_boundaries
from backend.translation import split_source, PatchError


class SentenceUpdateTests(unittest.TestCase):
    def document(self):
        units = [Segment("a", "If it rains, ", "如果下雨，"),
                 Segment("b", "we stay home. ", "我们就待在家里。"),
                 Segment("c", "The budget is unchanged.", "预算不变。")]
        source = "".join(s.source for s in units)
        return Document(source=source, baseline=source, segments=units)

    def commit(self, document, translations):
        request = prepare_request(document)
        self.assertEqual(request.mode, "sentence")
        raw = json.dumps({"translations": [{"segment_id": sid, "target": text} for sid, text in translations.items()]})
        response = parse_response(request, raw, "test")
        self.assertTrue(apply_response(document, request, response))
        return request

    def test_inserting_words_sends_and_replaces_the_whole_sentence_without_anchors(self):
        document = self.document()
        untouched = document.segments[-1]
        document.edit(document.source.replace("stay home", "stay home all day"))
        request = prepare_request(document)
        payload = json.loads(build_prompt(request).split("\n", 1)[1])
        self.assertEqual(len(payload["units"]), 1)
        unit = payload["units"][0]
        self.assertEqual(unit["source_after"], "If it rains, we stay home all day. ")
        self.assertEqual(unit["target_text"], "如果下雨，我们就待在家里。")
        self.assertEqual(set(unit), {"segment_id", "source_before", "source_after", "target_text"})
        self.assertEqual(set(output_schema(request.mode)["properties"]), {"translations"})
        self.commit(document, {"a": "如果下雨，我们就整天待在家里。"})
        self.assertEqual(document.target, "如果下雨，我们就整天待在家里。预算不变。")
        self.assertEqual(document.segments[-1], untouched)
        self.assertEqual(len(document.segments), 2)
        self.assertFalse(document.pending)

    def test_chinese_update_replaces_full_english_and_preserves_sentence_spacing(self):
        document = self.document()
        document.edit_target(document.target.replace("下雨", "下大雨"))
        request = self.commit(document, {"a": "If it rains heavily, we stay home."})
        self.assertEqual(request.source_language, "zh")
        self.assertEqual(request.sentences[0].source_after, "如果下大雨，我们就待在家里。")
        self.assertEqual(document.source, "If it rains heavily, we stay home. The budget is unchanged.")
        self.assertFalse(document.pending)

    def test_appending_a_clause_before_period_does_not_pull_in_next_sentence(self):
        document = self.document()
        document.edit_target(document.target.replace("待在家里。", "待在家里，并保存全部原始记录。"))
        request = prepare_request(document)
        self.assertEqual(request.sentences[0].segment_ids, ("a", "b"))
        self.assertNotIn("预算", request.sentences[0].source_after)
        self.commit(document, {"a": "If it rains, we stay home and save all raw records."})
        self.assertEqual(document.segments[-1], Segment("c", "The budget is unchanged.", "预算不变。"))

    def test_legacy_160_character_chunks_are_joined_into_one_long_sentence(self):
        source = "We carefully review " + "every available observation, " * 15 + "before making a decision. Next sentence."
        units = [Segment(s.id, s.source, "旧译文。" if s.source.strip() else s.source) for s in split_source(source)]
        document = Document(source=source, baseline=source, segments=units)
        document.edit(source.replace("carefully", "thoroughly"))
        request = prepare_request(document)
        self.assertEqual(len(request.sentences), 1)
        sentence = request.sentences[0]
        self.assertGreater(len(sentence.source_after), 400)
        self.assertTrue(sentence.source_after.startswith("We thoroughly review"))
        self.assertTrue(sentence.source_after.endswith("before making a decision. "))
        self.assertNotIn("Next sentence", sentence.source_after)

    def test_cross_sentence_deletion_and_merge_translates_the_complete_new_sentence(self):
        units = [Segment("a", "We wait. ", "我们等待。"), Segment("b", "Then we leave. ", "然后离开。"),
                 Segment("c", "The budget stays.", "预算保留。")]
        source = "".join(s.source for s in units)
        document = Document(source=source, baseline=source, segments=units)
        document.edit("We wait and then leave. The budget stays.")
        request = self.commit(document, {"b": "我们等待，然后离开。"})
        self.assertEqual(set(request.affected_ids), {"a", "b"})
        self.assertEqual([s.source_after for s in request.sentences if s.needs_translation],
                         ["We wait and then leave. "])
        self.assertEqual(document.target, "我们等待，然后离开。预算保留。")
        self.assertFalse(document.pending)

    def test_splitting_a_sentence_returns_both_new_sentences_as_one_replacement(self):
        document = self.document()
        document.edit(document.source.replace("If it rains, we stay home", "It rains. We stay home"))
        self.commit(document, {"a": "下雨了。我们待在家里。"})
        self.assertEqual(document.target, "下雨了。我们待在家里。预算不变。")

    def test_same_sentence_edits_on_both_sides_require_choice_even_in_different_clauses(self):
        for preferred in ("en", "zh"):
            document = self.document()
            document.edit(document.source.replace("rains", "snows"))
            document.edit_target(document.target.replace("待在家里", "留在办公室"))
            with self.assertRaises(ConflictingEdits):
                prepare_request(document)
            resolve_conflicts(document, preferred)
            self.assertEqual(prepare_request(document).source_language, preferred)

    def test_distinct_sentence_edits_on_both_sides_survive_sequential_replacements(self):
        document = self.document()
        document.edit_target(document.target.replace("下雨", "下大雨"))
        document.edit(document.source.replace("unchanged", "doubled"))
        self.commit(document, {"a": "If it rains heavily, we stay home."})
        self.assertTrue(document.pending)
        self.assertTrue(document.source.endswith("The budget is doubled."))
        self.commit(document, {"c": "预算翻倍。"})
        self.assertEqual(document.target, "如果下大雨，我们就待在家里。预算翻倍。")
        self.assertFalse(document.pending)

    def test_missing_extra_empty_or_legacy_patch_results_do_not_commit(self):
        document = self.document()
        document.edit(document.source.replace("rains", "snows"))
        request = prepare_request(document)
        before = document.to_dict()
        for translations in ({}, {"a": ""}, {"a": "如果下雪，我们就待在家里。", "c": "不应修改。"}):
            with self.subTest(translations=translations), self.assertRaises(PatchError):
                apply_response(document, request, TranslationResponse(document.id, document.revision, translations))
            self.assertEqual(document.to_dict(), before)
        with self.assertRaises(Exception):
            parse_response(request, '{"patches": []}', "test")

    def test_duplicate_sentence_results_are_rejected(self):
        document = self.document()
        document.edit(document.source.replace("rains", "snows"))
        request = prepare_request(document)
        raw = '{"translations": [{"segment_id": "a", "target": "下雪。"}, {"segment_id": "a", "target": "下雪。"}]}'
        with self.assertRaises(Exception):
            parse_response(request, raw, "test")

    def test_deleting_complete_sentence_is_local_and_preserves_remaining_text(self):
        document = self.document()
        document.edit("The budget is unchanged.")
        request = prepare_request(document)
        response = local_response(request)
        self.assertIsNotNone(response)
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, "预算不变。")
        self.assertFalse(document.pending)

    def test_new_input_in_either_pane_makes_whole_sentence_response_stale(self):
        for side in ("en", "zh"):
            document = self.document()
            document.edit(document.source.replace("rains", "snows"))
            request = prepare_request(document)
            if side == "en":
                document.edit(document.source + " Another sentence.")
            else:
                document.edit_target(document.target + "另一句。")
            before = document.to_dict()
            self.assertFalse(apply_response(document, request, TranslationResponse(document.id, request.revision,
                             {"a": "如果下雪，我们就待在家里。"})))
            self.assertEqual(document.to_dict(), before)

    def test_sentence_boundaries_preserve_abbreviations_decimals_code_and_soft_wraps(self):
        text = "Dr. Smith measured 3.14 units,\nand checked `x. y` again. Next sentence."
        ends = sentence_boundaries(text)
        for offset in (text.index("Dr.") + 3, text.index("3.") + 2, text.index("\n") + 1, text.index("x.") + 2):
            self.assertNotIn(offset, ends)
        self.assertIn(text.index(" Next"), ends)
        self.assertIn(text.index("Next"), ends)


if __name__ == "__main__":
    unittest.main()
