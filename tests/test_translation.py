from pathlib import Path
import random
import tempfile
import unittest

from backend.models import Document, Segment, TargetPatch, TranslationResponse
from backend.providers.protocol import build_prompt, local_response
from backend.storage import load_document, save_document
from backend.translation import PatchError, apply_response, locate_patch, prepare_request, project_sources, split_source


def translated_document():
    return Document(source="We finish in three days. Budget unchanged.",
                    baseline="We finish in three days. Budget unchanged.",
                    segments=[Segment("a", "We finish in three days. ", "我们三天内完成。 "),
                              Segment("b", "Budget unchanged.", "预算不变。")])


class IncrementalTranslationTests(unittest.TestCase):
    def test_phrase_update_preserves_every_other_character(self):
        document = translated_document()
        original = document.target
        document.edit(document.source.replace("three", "five"))
        request = prepare_request(document)
        response = TranslationResponse(document.id, document.revision,
                                       patches=(TargetPatch("a", "三", "五", "我们", "天"),))
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, original.replace("三", "五"))
        self.assertEqual(document.baseline, document.source)

    def test_stale_response_is_discarded(self):
        document = translated_document()
        document.edit(document.source.replace("three", "five"))
        request = prepare_request(document)
        document.edit(document.source.replace("five", "six"))
        response = TranslationResponse(request.document_id, request.revision,
                                       patches=(TargetPatch("a", "三", "五"),))
        before = document.to_dict()
        self.assertFalse(apply_response(document, request, response))
        self.assertEqual(document.to_dict(), before)

    def test_response_for_another_document_is_discarded(self):
        document = translated_document()
        document.edit(document.source.replace("three", "five"))
        request = prepare_request(document)
        other = translated_document()
        self.assertFalse(apply_response(other, request, TranslationResponse(document.id, document.revision)))

    def test_unaffected_unit_is_not_writable(self):
        document = translated_document()
        document.edit(document.source.replace("three", "five"))
        request = prepare_request(document)
        before = document.to_dict()
        response = TranslationResponse(document.id, document.revision,
                                       patches=(TargetPatch("b", "预算", "经费"),))
        with self.assertRaises(PatchError):
            apply_response(document, request, response)
        self.assertEqual(document.to_dict(), before)

    def test_edited_sentence_can_rewrite_its_corresponding_chinese(self):
        document = translated_document()
        document.edit(document.source.replace("three", "five"))
        request = prepare_request(document)
        response = TranslationResponse(document.id, document.revision,
                                       patches=(TargetPatch("a", document.segments[0].target, "五日即可完工。"),))
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, "五日即可完工。预算不变。")
        self.assertEqual(document.segments[1].target, "预算不变。")

    def test_expanded_sentence_has_no_fixed_chinese_character_budget(self):
        document = translated_document()
        document.edit(document.source.replace("We finish in three days. ",
                      "After confirming the schedule with the client, we will finish in five working days. "))
        request = prepare_request(document)
        new_target = "在与客户确认项目进度安排之后，我们将在五个工作日内完成全部任务。 "
        response = TranslationResponse(document.id, document.revision, patches=(
            TargetPatch("a", document.segments[0].target, new_target),))
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, new_target + "预算不变。")

    def test_delete_entire_paragraph_locally_preserves_other_paragraphs(self):
        parts = [Segment("a", "First sentence. ", "第一句话。 "),
                 Segment("b", "Second sentence.\n\n", "第二句话。\n\n"),
                 Segment("c", "Next paragraph.", "下一段。")]
        source = "".join(s.source for s in parts)
        document = Document(source=source, baseline=source, segments=parts)
        document.edit("Next paragraph.")
        request = prepare_request(document)
        self.assertEqual(request.mode, "revise")
        response = local_response(request)
        self.assertIsNotNone(response)
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, "下一段。")
        self.assertEqual(document.segments, [parts[-1]])

    def test_nonadjacent_sentence_deletions_are_one_local_update(self):
        parts = [Segment(str(i), f"Sentence {i}.\n", f"第{i}句。\n") for i in range(1, 6)]
        source = "".join(s.source for s in parts)
        document = Document(source=source, baseline=source, segments=parts)
        kept = [parts[0], parts[2], parts[4]]
        document.edit("".join(s.source for s in kept))
        request = prepare_request(document)
        response = local_response(request)
        self.assertIsNotNone(response)
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.segments, kept)

    def test_deletion_and_sentence_rewrite_commit_together(self):
        parts = [Segment("a", "We finish in three days.\n", "我们三天内完成。\n"),
                 Segment("b", "Remove this sentence.\n", "删除这句话。\n"),
                 Segment("c", "Budget unchanged.", "预算不变。")]
        source = "".join(s.source for s in parts)
        document = Document(source=source, baseline=source, segments=parts)
        document.edit("We finish in five days.\nBudget unchanged.")
        request = prepare_request(document)
        self.assertIsNone(local_response(request))
        self.assertIn('"locally_deleted":true', build_prompt(request))
        response = TranslationResponse(document.id, document.revision,
                         patches=(TargetPatch("a", parts[0].target, "五天内即可完工。\n"),))
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, "五天内即可完工。\n预算不变。")
        self.assertEqual(document.segments[-1], parts[-1])

    def test_deleting_all_text_needs_no_model_patches(self):
        document = translated_document()
        document.edit("")
        request = prepare_request(document)
        response = local_response(request)
        self.assertIsNotNone(response)
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, "")
        self.assertEqual(document.segments, [])
        self.assertFalse(document.pending)

    def test_retranslation_payload_is_rejected_in_revision_mode(self):
        document = translated_document()
        document.edit(document.source.replace("three", "five"))
        request = prepare_request(document)
        with self.assertRaises(PatchError):
            apply_response(document, request, TranslationResponse(document.id, document.revision,
                                                                  translations={"a": "新译文"}))

    def test_repeated_phrase_needs_unique_anchor(self):
        document = Document(source="Three days and three nights.", baseline="Three days and three nights.",
                            segments=[Segment("a", "Three days and three nights.", "三天和三夜。")])
        document.edit("Five days and three nights.")
        request = prepare_request(document)
        with self.assertRaises(PatchError):
            apply_response(document, request, TranslationResponse(document.id, document.revision,
                                                                  patches=(TargetPatch("a", "三", "五"),)))
        response = TranslationResponse(document.id, document.revision,
                                       patches=(TargetPatch("a", "三", "五", right="天"),))
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, "五天和三夜。")

    def test_overlapping_patches_are_atomic(self):
        document = translated_document()
        document.edit(document.source.replace("three", "five"))
        request = prepare_request(document)
        before = document.to_dict()
        response = TranslationResponse(document.id, document.revision, patches=(
            TargetPatch("a", "三天", "五日"), TargetPatch("a", "天", "日")))
        with self.assertRaises(PatchError):
            apply_response(document, request, response)
        self.assertEqual(document.to_dict(), before)

    def test_insertion_keeps_existing_target(self):
        document = translated_document()
        document.edit(document.source.replace("three days", "just three days"))
        request = prepare_request(document)
        response = TranslationResponse(document.id, document.revision,
                                       patches=(TargetPatch("a", "", "仅", "我们", "三天"),))
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, "我们仅三天内完成。 预算不变。")

    def test_removing_all_source_deletes_all_target(self):
        document = translated_document()
        document.edit("")
        request = prepare_request(document)
        response = TranslationResponse(document.id, document.revision, patches=tuple(
            TargetPatch(s.id, s.target, "") for s in document.segments))
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, "")
        self.assertFalse(document.pending)

    def test_whitespace_only_document_does_not_need_model(self):
        document = Document(source="\n \n")
        request = prepare_request(document)
        self.assertTrue(apply_response(document, request, TranslationResponse(document.id, document.revision)))
        self.assertEqual(document.target, document.source)

    def test_first_text_after_synced_blank_lines_uses_initial_translation(self):
        document = Document(source="\n\n")
        request = prepare_request(document)
        apply_response(document, request, TranslationResponse(document.id, document.revision))
        document.edit("hello\n\n")
        request = prepare_request(document)
        self.assertEqual(request.mode, "initial")
        response = TranslationResponse(document.id, document.revision,
                                       translations={s.id: s.source.replace("hello", "你好")
                                                     for s in request.segments if s.source.strip()})
        self.assertTrue(apply_response(document, request, response))
        self.assertEqual(document.target, "你好\n\n")

    def test_anchorless_insertion_has_unique_position_only_in_empty_target(self):
        patch = TargetPatch("a", "", "你好")
        self.assertEqual(locate_patch("", patch), (0, 0, "你好"))
        for target in ("\n", "已有中文"):
            with self.subTest(target=target), self.assertRaises(PatchError):
                locate_patch(target, patch)

    def test_anchorless_insertion_into_existing_chinese_is_rejected_atomically(self):
        document = translated_document()
        document.edit(document.source.replace("three days", "just three days"))
        request = prepare_request(document)
        before = document.to_dict()
        with self.assertRaises(PatchError):
            apply_response(document, request, TranslationResponse(document.id, document.revision,
                           patches=(TargetPatch("a", "", "仅"),)))
        self.assertEqual(document.to_dict(), before)

    def test_blank_baseline_with_existing_chinese_does_not_trigger_retranslation(self):
        document = Document(source="hello\n", baseline="\n",
                            segments=[Segment("a", "\n", "已有中文\n")])
        self.assertEqual(prepare_request(document).mode, "revise")

    def test_initial_missing_unit_does_not_commit(self):
        document = Document(source="A. B.")
        request = prepare_request(document)
        with self.assertRaises(PatchError):
            apply_response(document, request, TranslationResponse(document.id, document.revision))
        self.assertEqual(document.baseline, "")

    def test_initial_translation_and_line_breaks(self):
        document = Document(source="Hello.\nWorld.")
        request = prepare_request(document)
        targets = {s.id: {"Hello.": "你好。", "World.": "世界。"}[s.source]
                   for s in request.segments if s.source.strip()}
        self.assertTrue(apply_response(document, request, TranslationResponse(document.id, document.revision,
                                                                              translations=targets)))
        self.assertEqual(document.target, "你好。\n世界。")

    def test_projection_preserves_insert_delete_and_cross_boundary_changes(self):
        before = "One, two.\nThree; four."
        segments = split_source(before)
        for after in ("Start. " + before, before + " End.", "", "One, four.", "One\ntwo.\nThree; four."):
            with self.subTest(after=after):
                updated, _ = project_sources(segments, before, after)
                self.assertEqual("".join(s.source for s in updated), after)

    def test_projection_random_edits_preserve_exact_source(self):
        generator = random.Random(42)
        source = "First sentence. A second, longer sentence.\nThird line."
        segments = split_source(source)
        for _ in range(200):
            start = generator.randrange(len(source) + 1)
            end = generator.randrange(start, len(source) + 1)
            insertion = generator.choice(["", "new text", "\n", "🙂", "A. B."])
            after = source[:start] + insertion + source[end:]
            updated, _ = project_sources(segments, source, after)
            self.assertEqual("".join(s.source for s in updated), after)

    def test_json_roundtrip_preserves_pending_changes_and_alignment(self):
        document = translated_document()
        document.edit(document.source.replace("three", "five"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "document.json"
            save_document(path, document)
            recovered = load_document(path)
        self.assertEqual(recovered.to_dict(), document.to_dict())
        self.assertIsNotNone(prepare_request(recovered))


if __name__ == "__main__":
    unittest.main()
