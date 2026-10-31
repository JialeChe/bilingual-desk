import unittest

from backend.models import Segment
from backend.paragraphs import paragraph_pairs
from backend.translation import split_source


class ParagraphTests(unittest.TestCase):
    def assert_preserved(self, pairs, source, target):
        self.assertEqual("".join(p.source for p in pairs), source)
        self.assertEqual("".join(p.target for p in pairs), target)

    def test_paragraphs_follow_existing_newlines(self):
        source, target = "First.\n\nSecond.\nThird.", "第一。\n\n第二。\n第三。"
        pairs = paragraph_pairs(source, source, (Segment("a", source, target),))
        self.assertEqual(len(pairs), 3)
        self.assertEqual(pairs[0].source, "First.\n\n")
        self.assert_preserved(pairs, source, target)

    def test_long_text_is_automatically_grouped_without_changing_text(self):
        source = "An English sentence with some useful context. " * 30
        units = tuple(Segment(s.id, s.source, "一句中文。 ") for s in split_source(source))
        pairs = paragraph_pairs(source, source, units)
        self.assertGreater(len(pairs), 2)
        self.assertTrue(all(p.source.endswith(". ") for p in pairs))
        self.assert_preserved(pairs, source, "".join(s.target for s in units))

    def test_pending_edit_preserves_old_chinese_and_other_paragraphs(self):
        source = "Three days.\nBudget unchanged."
        units = (Segment("a", "Three days.\n", "三天。\n"),
                 Segment("b", "Budget unchanged.", "预算不变。"))
        edited = source.replace("Three", "Five")
        pairs = paragraph_pairs(edited, source, units)
        self.assertEqual(pairs[0].source, "Five days.\n")
        self.assertEqual(pairs[1].target, "预算不变。")
        self.assert_preserved(pairs, edited, "三天。\n预算不变。")

    def test_pending_newline_does_not_guess_a_chinese_character_cut(self):
        unit = Segment("a", "First. Second.", "第一。第二。")
        edited = "First.\nSecond."
        pairs = paragraph_pairs(edited, unit.source, (unit,))
        self.assertEqual(len(pairs), 1)
        self.assert_preserved(pairs, edited, unit.target)

    def test_deletion_keeps_pending_target_visible(self):
        unit = Segment("a", "First.", "第一。")
        self.assert_preserved(paragraph_pairs("", unit.source, (unit,)), "", unit.target)

    def test_empty_untranslated_and_crlf_are_lossless(self):
        for source in ("", "\n\n", "First.\r\n\r\nSecond.", "word " * 300):
            pairs = paragraph_pairs(source)
            self.assertEqual("".join(p.source for p in pairs), source)
            self.assertGreaterEqual(len(pairs), 1)


if __name__ == "__main__":
    unittest.main()
