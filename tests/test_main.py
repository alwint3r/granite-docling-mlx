import tempfile
import unittest
from pathlib import Path

from PIL import Image
from docling_core.types.doc.document import DocTagsDocument, DoclingDocument

from main import (
    Continuation,
    PageSummary,
    TableBoundary,
    annotate_continuations,
    detect_table_continuations,
    navigation_markdown,
    summarize_page,
    wrap_page_markdown,
)


def table(
    *,
    top: float = 0.02,
    bottom: float = 0.98,
    columns: int = 3,
    headers: tuple[str, ...] = ("name", "date", "amount"),
    caption: str = "",
    leading_text: str = "",
) -> TableBoundary:
    return TableBoundary(
        left=0.10,
        top=top,
        right=0.90,
        bottom=bottom,
        num_cols=columns,
        headers=headers,
        caption=caption,
        leading_text=leading_text,
    )


class ContinuationDetectionTests(unittest.TestCase):
    def summaries(
        self, previous_table: TableBoundary, current_table: TableBoundary
    ) -> tuple[PageSummary, PageSummary]:
        return (
            PageSummary(1, Path("doc-page-0001.md"), (), (previous_table,)),
            PageSummary(2, Path("doc-page-0002.md"), (current_table,), ()),
        )

    def test_detects_aligned_table_with_repeated_headers(self):
        previous, current = self.summaries(
            table(top=0.50), table(bottom=0.50)
        )

        continuations = detect_table_continuations(previous, current)

        self.assertEqual(len(continuations), 1)
        self.assertFalse(continuations[0].confirmed)
        self.assertGreaterEqual(continuations[0].confidence, 0.80)

    def test_rejects_tables_with_different_structures(self):
        previous, current = self.summaries(
            table(top=0.50),
            table(
                bottom=0.50,
                columns=2,
                headers=("unrelated", "values"),
            ),
        )

        self.assertEqual(detect_table_continuations(previous, current), [])

    def test_explicit_continued_caption_confirms_match(self):
        previous, current = self.summaries(
            table(top=0.50, caption="Table 4"),
            table(bottom=0.50, caption="Table 4 (cont.)"),
        )

        continuations = detect_table_continuations(previous, current)

        self.assertEqual(len(continuations), 1)
        self.assertTrue(continuations[0].confirmed)
        self.assertEqual(continuations[0].confidence, 1.0)

    def test_caption_ending_in_continued_is_an_explicit_marker(self):
        boundary = table(
            caption="Table 4 continued",
            leading_text="Name Date Amount",
        )

        self.assertTrue(boundary.has_explicit_continuation)

    def test_ordinary_use_of_continued_is_not_an_explicit_marker(self):
        boundary = table(leading_text="Sales continued to rise in June")

        self.assertFalse(boundary.has_explicit_continuation)


class PageSummaryTests(unittest.TestCase):
    def test_extracts_bottom_table_metadata_from_docling_document(self):
        doctags = (
            "<doctag><otsl><loc_50><loc_400><loc_450><loc_490>"
            "<ched>Name<ched>Date<ched>Amount<nl>"
            "<fcel>A<fcel>B<fcel>C</otsl></doctag>"
        )
        image = Image.new("RGB", (1000, 1000), "white")
        document = DoclingDocument.load_from_doctags(
            DocTagsDocument.from_doctags_and_image_pairs([doctags], [image])
        )

        summary = summarize_page(document, 7, Path("doc-page-0007.md"))

        self.assertEqual(summary.top_tables, ())
        self.assertEqual(len(summary.bottom_tables), 1)
        boundary = summary.bottom_tables[0]
        self.assertEqual(boundary.num_cols, 3)
        self.assertEqual(boundary.headers, ("name", "date", "amount"))
        self.assertAlmostEqual(boundary.bottom, 0.98)


class MarkdownAnnotationTests(unittest.TestCase):
    def test_navigation_links_adjacent_page_files(self):
        navigation = navigation_markdown(2, 3, "report", 4)

        self.assertIn("[← Page 1](report-page-0001.md)", navigation)
        self.assertIn("Page 2 of 3", navigation)
        self.assertIn("[Page 3 →](report-page-0003.md)", navigation)

    def test_annotations_are_added_to_both_pages(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            previous_path = directory / "doc-page-0001.md"
            current_path = directory / "doc-page-0002.md"
            previous_path.write_text(
                wrap_page_markdown("previous", "Page 1 of 2"), encoding="utf-8"
            )
            current_path.write_text(
                wrap_page_markdown("current", "Page 2 of 2"), encoding="utf-8"
            )
            previous_table = table(top=0.50)
            current_table = table(bottom=0.50)
            previous = PageSummary(1, previous_path, (), (previous_table,))
            current = PageSummary(2, current_path, (current_table,), ())
            continuation = Continuation(
                previous_table,
                current_table,
                confidence=0.9,
                confirmed=False,
            )

            annotate_continuations(previous, current, [continuation])

            previous_markdown = previous_path.read_text(encoding="utf-8")
            current_markdown = current_path.read_text(encoding="utf-8")
            self.assertIn("Likely continuation", previous_markdown)
            self.assertIn("[page 2](doc-page-0002.md)", previous_markdown)
            self.assertIn("Likely continuation", current_markdown)
            self.assertIn("[page 1](doc-page-0001.md)", current_markdown)


if __name__ == "__main__":
    unittest.main()
