import json
from pathlib import Path
import tempfile
import unittest

from backend.bilingual import prepare_request, apply_response, resolve_conflicts, ConflictingEdits
from backend.models import Document, Segment, TargetPatch, TranslationResponse
from backend.providers.protocol import local_response, build_prompt
from backend.storage import load_document, save_document


class BilingualTests(unittest.TestCase):
    def document(self):
        units = [Segment("a", "Three days.\n", "三天。\n"),
                 Segment("b", "Budget unchanged.", "预算不变。")]
        return Document(source="".join(s.source for s in units),
                        baseline="".join(s.source for s in units), segments=units)

    def apply_patch(self, document, segment, before, after, language):
        request = prepare_request(document)
        self.assertEqual(request.source_language, language)
        payload = json.loads(build_prompt(request).split("\n", 1)[1])
        self.assertEqual(payload["target_language"], "en" if language == "zh" else "zh")
        self.assertEqual(request.mode, "sentence")
        sentence = next(s for s in request.sentences if segment in s.segment_ids)
        self.assertTrue(apply_response(document, request, TranslationResponse(document.id, document.revision,
                        translations={sentence.id: sentence.target_text.replace(before, after)})))

    def test_initial_chinese_then_updates_from_both_sides(self):
        document = Document(target_text="三天。")
        request = prepare_request(document)
        response = TranslationResponse(document.id, document.revision,
                                       {request.segments[0].id: "Three days."})
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual((document.target, document.source), ("三天。", "Three days."))
        self.assertFalse(document.pending)
        sid = document.segments[0].id
        document.edit_target("五天。")
        self.apply_patch(document, sid, "Three", "Five", "zh")
        document.edit("Six days.")
        self.apply_patch(document, sid, "五", "六", "en")
        self.assertEqual(document.target, "六天。")
        self.assertIsNone(prepare_request(document))

    def test_initial_english(self):
        document = Document(source="Three days.")
        request = prepare_request(document)
        self.assertEqual(request.source_language, "en")
        self.assertTrue(apply_response(document, request, TranslationResponse(document.id, document.revision,
                        {request.segments[0].id: "三天。"})))
        self.assertFalse(document.pending)

    def test_chinese_clauses_get_english_word_and_sentence_spacing(self):
        document = Document(target_text="结果表明，分批有效。继续。\n结尾。")
        request = prepare_request(document)
        mapping = {"结果表明，": "Results show that", "分批有效。": "batching works.",
                   "继续。": "Continue.", "结尾。": "The end."}
        response = TranslationResponse(document.id, document.revision,
            {s.id: mapping[s.source] for s in request.segments if s.source.strip()})
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.source, "Results show that batching works. Continue.\nThe end.")
        self.assertFalse(document.pending)

    def test_disjoint_edits_preserve_pending_opposite_side_and_finish_both(self):
        document = self.document()
        document.edit_target("五天。\n预算不变。")
        document.edit("Three days.\nBudget doubled.")
        self.apply_patch(document, "a", "Three", "Five", "zh")
        self.assertEqual(document.source, "Five days.\nBudget doubled.")
        self.assertEqual(document.baseline, "Five days.\nBudget unchanged.")
        self.assertTrue(document.pending)
        self.apply_patch(document, "b", "不变", "翻倍", "en")
        self.assertEqual(document.target, "五天。\n预算翻倍。")
        self.assertFalse(document.pending)

    def test_conflicts_require_explicit_choice_and_only_discard_overlapping_edits(self):
        for preferred in ("zh", "en"):
            document = self.document()
            document.edit("Six days.\nBudget doubled.")
            document.edit_target("五天。\n预算不变。")
            before = document.to_dict()
            with self.assertRaises(ConflictingEdits):
                prepare_request(document)
            self.assertEqual(before, document.to_dict())
            resolve_conflicts(document, preferred)
            self.assertIn("Budget doubled.", document.source)
            self.assertEqual(document.target, "五天。\n预算不变。" if preferred == "zh" else "三天。\n预算不变。")
            self.assertEqual(prepare_request(document).source_language, preferred)

    def test_initial_two_nonempty_sides_require_choice(self):
        document = Document(source="Six days.", target_text="五天。")
        with self.assertRaises(ConflictingEdits):
            prepare_request(document)
        resolve_conflicts(document, "zh")
        self.assertEqual(document.source, "")
        self.assertEqual(prepare_request(document).source_language, "zh")

    def test_either_side_edit_invalidates_response(self):
        for side in ("zh", "en"):
            document = self.document()
            document.edit_target(document.target.replace("三", "五"))
            request = prepare_request(document)
            (document.edit_target if side == "zh" else document.edit)("A newer edit.")
            before = document.to_dict()
            self.assertFalse(apply_response(document, request, TranslationResponse(document.id, request.revision)))
            self.assertEqual(document.to_dict(), before)

    def test_deletion_and_whitespace_work_in_both_directions_without_provider(self):
        for side in ("zh", "en"):
            for text in ("", "\n"):
                document = self.document()
                (document.edit_target if side == "zh" else document.edit)(text)
                request = prepare_request(document)
                self.assertTrue(apply_response(document, request, local_response(request)))
                self.assertEqual((document.source, document.target), (text, text))
                self.assertFalse(document.pending)

    def test_repair_does_not_overwrite_an_unprocessed_opposite_edit(self):
        document = Document(source="Three days.\n预算不变。", baseline="Three days.\n预算不变。",
                            segments=[Segment("a", "Three days.\n", "三天。\n"),
                                      Segment("b", "预算不变。", "预算不变。")])
        document.edit_target("五天。\n预算不变。")
        document.edit("Three days.\nBudget doubled.")
        request = prepare_request(document)
        self.assertNotIn("b", request.affected_ids)
        self.apply_patch(document, "a", "Three", "Five", "zh")
        self.assertIn("Budget doubled.", document.source)
        self.assertEqual(prepare_request(document).source_language, "en")

    def test_save_load_pending_both_sides_and_legacy_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "document.json"
            document = self.document()
            document.edit_target("五天。\n预算不变。")
            document.edit("Three days.\nBudget doubled.")
            save_document(path, document)
            loaded = load_document(path)
            self.assertEqual(loaded.to_dict(), document.to_dict())
            self.assertEqual(prepare_request(loaded).source_language, "zh")
            legacy = self.document().to_dict()
            legacy["format_version"] = 1
            legacy.pop("target_text")
            path.write_text(json.dumps(legacy), encoding="utf-8")
            self.assertFalse(load_document(path).pending)


if __name__ == "__main__":
    unittest.main()
