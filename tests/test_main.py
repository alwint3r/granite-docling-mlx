import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from docling_core.types.doc.document import DocTagsDocument, DoclingDocument

from main import (
    Continuation,
    PageSummary,
    PendingDocumentPage,
    TableBoundary,
    annotate_page_continuations,
    convert_pdf,
    detect_table_continuations,
    export_page_markdown,
    navigation_markdown,
    parse_args,
    render_document_page,
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
            PageSummary(1, (), (previous_table,)),
            PageSummary(2, (current_table,), ()),
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

        summary = summarize_page(document, 7)

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
            previous = PageSummary(1, (), (previous_table,))
            current = PageSummary(2, (current_table,), ())
            continuation = Continuation(
                previous_table,
                current_table,
                confidence=0.9,
                confirmed=False,
            )

            annotate_page_continuations(
                previous,
                current,
                previous_path,
                current_path,
                [continuation],
            )

            previous_markdown = previous_path.read_text(encoding="utf-8")
            current_markdown = current_path.read_text(encoding="utf-8")
            self.assertIn("Likely continuation", previous_markdown)
            self.assertIn("[page 2](doc-page-0002.md)", previous_markdown)
            self.assertIn("Likely continuation", current_markdown)
            self.assertIn("[page 1](doc-page-0001.md)", current_markdown)


class CliArgumentTests(unittest.TestCase):
    def test_document_mode_is_default(self):
        args = parse_args(["input.pdf", "--output-dir", "output"])

        self.assertEqual(args.output_mode, "document")

    def test_pages_mode_can_be_selected(self):
        args = parse_args(
            [
                "input.pdf",
                "--output-dir",
                "output",
                "--output-mode",
                "pages",
            ]
        )

        self.assertEqual(args.output_mode, "pages")

    def test_invalid_output_mode_is_rejected(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(
                [
                    "input.pdf",
                    "--output-dir",
                    "output",
                    "--output-mode",
                    "pdf",
                ]
            )


class DocumentMarkdownTests(unittest.TestCase):
    def test_document_page_uses_internal_navigation_and_continuation_links(self):
        previous_table = table(top=0.50)
        current_table = table(bottom=0.50)
        continuation = Continuation(
            previous_table,
            current_table,
            confidence=0.9,
            confirmed=False,
        )
        pending = PendingDocumentPage(
            page_number=2,
            markdown="Page two",
            summary=PageSummary(2, (current_table,), ()),
            incoming_continuations=(continuation,),
        )

        markdown = render_document_page(pending, [continuation], 3)

        self.assertIn('<a id="page-2"></a>', markdown)
        self.assertIn("[← Page 1](#page-1)", markdown)
        self.assertIn("[Page 3 →](#page-3)", markdown)
        self.assertIn("[page 1](#page-1)", markdown)
        self.assertIn("[page 3](#page-3)", markdown)
        self.assertIn("<!-- PDF page break -->", markdown)

    def test_final_document_page_has_no_page_break(self):
        pending = PendingDocumentPage(
            page_number=2,
            markdown="Last page",
            summary=PageSummary(2, (), ()),
        )

        markdown = render_document_page(pending, (), 2)

        self.assertNotIn("<!-- PDF page break -->", markdown)

    def test_temporary_page_export_references_final_artifact_directory(self):
        doctags = (
            "<doctag><picture><loc_50><loc_50><loc_200><loc_200>"
            "</picture></doctag>"
        )
        image = Image.new("RGB", (500, 500), "white")
        document = DoclingDocument.load_from_doctags(
            DocTagsDocument.from_doctags_and_image_pairs([doctags], [image])
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            temporary_markdown = directory / ".page.export.tmp.md"
            markdown = export_page_markdown(
                document,
                temporary_markdown,
                Path("figures") / "page-0001",
            )

            self.assertIn("](figures/page-0001/", markdown)
            figures = list((directory / "figures" / "page-0001").glob("*.png"))
            self.assertEqual(len(figures), 1)


class OutputModeIntegrationTests(unittest.TestCase):
    def make_pdf(self, path: Path) -> None:
        pages = [Image.new("RGB", (200, 300), "white") for _ in range(2)]
        pages[0].save(
            path,
            "PDF",
            save_all=True,
            append_images=pages[1:],
            resolution=72,
        )

    def fake_doctags(self, *args, **kwargs) -> str:
        page_number = args[4]
        return (
            "<doctag><text><loc_20><loc_20><loc_180><loc_60>"
            f"Test page {page_number}</text></doctag>"
        )

    def conversion_patches(self):
        processor = SimpleNamespace(detokenizer=None)
        return (
            patch("main.load", return_value=(object(), processor)),
            patch("main.load_config", return_value={}),
            patch("main.apply_chat_template", return_value="prompt"),
            patch("main.generate_doctags", side_effect=self.fake_doctags),
            patch("main.mx.clear_cache"),
        )

    def test_both_output_modes_create_expected_markdown_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            pdf_path = directory / "sample.pdf"
            self.make_pdf(pdf_path)
            document_output = directory / "document"
            pages_output = directory / "pages"

            patches = self.conversion_patches()
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                document_result = convert_pdf(
                    pdf_path,
                    document_output,
                    dpi=72,
                    model_path="test-model",
                    output_mode="document",
                )
                pages_result = convert_pdf(
                    pdf_path,
                    pages_output,
                    dpi=72,
                    model_path="test-model",
                    output_mode="pages",
                )

            document_files = sorted(document_output.glob("*.md"))
            page_files = sorted(pages_output.glob("*.md"))
            self.assertEqual(document_files, [document_output / "sample.md"])
            self.assertEqual(
                page_files,
                [
                    pages_output / "sample-page-0001.md",
                    pages_output / "sample-page-0002.md",
                ],
            )
            document_markdown = document_files[0].read_text(encoding="utf-8")
            self.assertIn("Test page 1", document_markdown)
            self.assertIn("Test page 2", document_markdown)
            self.assertEqual(document_result.markdown_destination, document_files[0])
            self.assertEqual(pages_result.markdown_destination, pages_output)
            self.assertFalse(any(document_output.glob("*.tmp.md")))

    def test_failed_document_conversion_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            pdf_path = directory / "sample.pdf"
            self.make_pdf(pdf_path)
            output_dir = directory / "output"
            output_dir.mkdir()
            document_path = output_dir / "sample.md"
            document_path.write_text("existing document", encoding="utf-8")

            def fail_on_second_page(*args, **kwargs):
                if args[4] == 2:
                    raise RuntimeError("generation failed")
                return self.fake_doctags(*args, **kwargs)

            patches = self.conversion_patches()
            with (
                patches[0],
                patches[1],
                patches[2],
                patch("main.generate_doctags", side_effect=fail_on_second_page),
                patches[4],
                self.assertRaisesRegex(RuntimeError, "generation failed"),
            ):
                convert_pdf(
                    pdf_path,
                    output_dir,
                    dpi=72,
                    model_path="test-model",
                    output_mode="document",
                )

            self.assertEqual(
                document_path.read_text(encoding="utf-8"), "existing document"
            )
            self.assertFalse((output_dir / ".sample.md.tmp").exists())


if __name__ == "__main__":
    unittest.main()
