import unittest

from backend.markdown import analyze_markdown
from backend.models import Document, Segment
from backend.paragraphs import markdown_pairs
from backend.providers.protocol import build_prompt
from backend.translation import prepare_request


class MarkdownTests(unittest.TestCase):
    def test_structure_is_lossless_and_prose_lines_stay_in_one_paragraph(self):
        text = "# Title\r\n\r\nFirst line\r\ncontinues here.\r\n\r\n- One\r\n- Two\r\n\r\n> Quote\r\n"
        blocks, _ = analyze_markdown(text)
        self.assertEqual([b.kind for b in blocks],
                         ["heading", "blank", "paragraph", "blank", "list", "list", "blank", "quote"])
        self.assertEqual("".join(text[b.start:b.end] for b in blocks), text)
        self.assertIn("First line\r\ncontinues here.\r\n", [text[b.start:b.end] for b in blocks])

    def test_fenced_code_does_not_parse_its_contents_as_headings_or_lists(self):
        for text in ("```python\n# comment\n- not a list\n```\n", "~~~~\n# comment\n~~~\n~~~~\n",
                     "```python\n# unfinished code"):
            with self.subTest(text=text):
                blocks, styles = analyze_markdown(text)
                self.assertEqual(len(blocks), 1)
                self.assertEqual(blocks[0].kind, "code")
                self.assertEqual(styles[0].style, "code")
                self.assertEqual(text[blocks[0].start:blocks[0].end], text)

    def test_inline_styles_keep_markdown_syntax_and_do_not_bold_inline_code(self):
        text = "A **bold** word and `**literal**` code."
        _, styles = analyze_markdown(text)
        strong = [text[s.start:s.end] for s in styles if s.style == "strong"]
        self.assertEqual(strong, ["**bold**"])
        self.assertIn("`**literal**`", [text[s.start:s.end] for s in styles])

    def test_bilingual_blocks_preserve_text_and_existing_chinese(self):
        source = "# Title\n\nA paragraph\ncontinued here.\n\n- One\n- Two\n"
        target = "# 标题\n\n一个段落\n在这里继续。\n\n- 第一项\n- 第二项\n"
        unit = Segment("a", source, target)
        pairs = markdown_pairs(source, source, (unit,))
        self.assertEqual(len(pairs), 4)
        self.assertEqual(pairs[1].source, "A paragraph\ncontinued here.\n\n")
        self.assertEqual("".join(p.source for p in pairs), source)
        self.assertEqual("".join(p.target for p in pairs), target)

    def test_pending_restructure_does_not_guess_chinese_sentence_boundaries(self):
        source, target = "One line.", "一行文字。"
        edited = "# One\n\nline."
        pairs = markdown_pairs(edited, source, (Segment("a", source, target),))
        self.assertEqual("".join(p.source for p in pairs), edited)
        self.assertEqual("".join(p.target for p in pairs), target)

    def test_translation_requests_include_backend_structure(self):
        request = prepare_request(Document(source="# Title\n\n- A list item\n"))
        prompt = build_prompt(request)
        self.assertIn('"block_kind":"heading"', prompt)
        self.assertIn('"block_kind":"list"', prompt)


if __name__ == "__main__":
    unittest.main()
