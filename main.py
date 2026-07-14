import argparse
import gc
import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import mlx.core as mx
import pypdfium2 as pdfium
from docling_core.types.doc import ImageRefMode
from docling_core.types.doc.document import (
    DocTagsDocument,
    DoclingDocument,
    TableItem,
)
from mlx_vlm import load, stream_generate
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.tokenizer_utils import BPEStreamingDetokenizer
from mlx_vlm.utils import load_config

DEFAULT_MODEL = "ibm-granite/granite-docling-258M-mlx"
PROMPT = "Convert this page to docling."

TOP_BOUNDARY = 0.20
BOTTOM_BOUNDARY = 0.80
CONTINUATION_THRESHOLD = 0.80
EXPLICIT_CONTINUATION_RE = re.compile(
    r"(?:\(\s*(?:continued|cont\.)\s*\)|\b(?:continued|cont\.)\s*$)",
    re.IGNORECASE,
)

CONTINUATION_FROM_MARKER = "<!-- continuation-from -->\n<!-- /continuation-from -->"
CONTINUATION_TO_MARKER = "<!-- continuation-to -->\n<!-- /continuation-to -->"


@dataclass(frozen=True)
class TableBoundary:
    left: float
    top: float
    right: float
    bottom: float
    num_cols: int
    headers: tuple[str, ...] = ()
    caption: str = ""
    leading_text: str = ""

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def has_explicit_continuation(self) -> bool:
        return any(
            EXPLICIT_CONTINUATION_RE.search(text) is not None
            for text in (self.caption, self.leading_text)
        )


@dataclass(frozen=True)
class PageSummary:
    page_number: int
    top_tables: tuple[TableBoundary, ...]
    bottom_tables: tuple[TableBoundary, ...]


@dataclass(frozen=True)
class Continuation:
    previous_table: TableBoundary
    next_table: TableBoundary
    confidence: float
    confirmed: bool


@dataclass(frozen=True)
class PendingDocumentPage:
    page_number: int
    markdown: str
    summary: PageSummary
    incoming_continuations: tuple[Continuation, ...] = ()


@dataclass(frozen=True)
class ConversionResult:
    page_count: int
    output_mode: str
    markdown_destination: Path


class Utf8SafeBPEStreamingDetokenizer(BPEStreamingDetokenizer):
    """Work around mlx-vlm failing on malformed generated UTF-8 bytes."""

    def add_token(self, token, skip_special_token_ids=None):
        if skip_special_token_ids is None:
            skip_special_token_ids = []
        if token in skip_special_token_ids:
            return

        value = self.tokenmap[token]
        if self._byte_decoder[value[0]] == 32:
            current_text = bytes(
                self._byte_decoder[char] for char in self._unflushed
            ).decode("utf-8", errors="ignore")
            if self.text or not self.trim_space:
                self.text += current_text
            else:
                self.text += current_text.removeprefix(" ")
            self._unflushed = value
        else:
            self._unflushed += value


def make_detokenizer_utf8_safe(processor) -> None:
    detokenizer = processor.detokenizer
    if isinstance(detokenizer, BPEStreamingDetokenizer):
        tokenizer = getattr(processor, "tokenizer", processor)
        processor.detokenizer = Utf8SafeBPEStreamingDetokenizer(
            tokenizer, trim_space=detokenizer.trim_space
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a PDF to Markdown, extract detected figures, and link "
            "likely cross-page tables."
        )
    )
    parser.add_argument("pdf", type=Path, help="PDF file to process")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for Markdown output and extracted figures",
    )
    parser.add_argument(
        "--output-mode",
        choices=("pages", "document"),
        default="document",
        help=(
            "write one file per page or one file for the whole document "
            "(default: document)"
        ),
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=144,
        help="PDF rendering resolution (default: 144)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"MLX model path or Hugging Face model ID (default: {DEFAULT_MODEL})",
    )
    return parser.parse_args(argv)


def page_markdown_path(
    pdf_path: Path, output_dir: Path, page_number: int, digits: int
) -> Path:
    return output_dir / f"{pdf_path.stem}-page-{page_number:0{digits}d}.md"


def generate_doctags(
    model,
    processor,
    formatted_prompt: str,
    page_image,
    page_number: int,
    page_count: int,
) -> str:
    print(f"Processing page {page_number}/{page_count}...")
    output = ""
    for token in stream_generate(
        model,
        processor,
        formatted_prompt,
        [page_image],
        max_tokens=65536,
        verbose=False,
    ):
        output += token.text
        if "</doctag>" in output:
            break
    return output


def normalize_text(text: str) -> str:
    return " ".join(re.findall(r"[\w]+", text.casefold()))


def text_similarity(left: str, right: str) -> float:
    normalized_left = normalize_text(left)
    normalized_right = normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def table_headers(table: TableItem) -> tuple[str, ...]:
    grid = table.data.grid
    if not grid:
        return ()

    header_rows = [
        row for row in grid if any(cell.column_header for cell in row)
    ]
    row = header_rows[0] if header_rows else grid[0]
    return tuple(normalize_text(cell.text) for cell in row)


def table_caption(document: DoclingDocument, table: TableItem) -> str:
    captions = []
    for caption_ref in table.captions:
        caption = caption_ref.resolve(document)
        text = getattr(caption, "text", "")
        if text:
            captions.append(text)
    return " ".join(captions)


def leading_table_text(table: TableItem, row_limit: int = 2) -> str:
    rows = table.data.grid[:row_limit]
    return " ".join(cell.text for row in rows for cell in row if cell.text)


def summarize_page(
    document: DoclingDocument, page_number: int
) -> PageSummary:
    page = document.pages.get(1)
    if page is None or page.size.width <= 0 or page.size.height <= 0:
        return PageSummary(page_number, (), ())

    top_tables = []
    bottom_tables = []
    for item, _ in document.iterate_items():
        if not isinstance(item, TableItem) or not item.prov:
            continue

        bbox = item.prov[0].bbox
        boundary = TableBoundary(
            left=max(0.0, min(1.0, bbox.l / page.size.width)),
            top=max(0.0, min(1.0, bbox.t / page.size.height)),
            right=max(0.0, min(1.0, bbox.r / page.size.width)),
            bottom=max(0.0, min(1.0, bbox.b / page.size.height)),
            num_cols=item.data.num_cols,
            headers=table_headers(item),
            caption=table_caption(document, item),
            leading_text=leading_table_text(item),
        )
        if boundary.top <= TOP_BOUNDARY:
            top_tables.append(boundary)
        if boundary.bottom >= BOTTOM_BOUNDARY:
            bottom_tables.append(boundary)

    return PageSummary(
        page_number=page_number,
        top_tables=tuple(top_tables),
        bottom_tables=tuple(bottom_tables),
    )


def horizontal_overlap(left: TableBoundary, right: TableBoundary) -> float:
    overlap = max(0.0, min(left.right, right.right) - max(left.left, right.left))
    smaller_width = min(left.width, right.width)
    if smaller_width <= 0:
        return 0.0
    return min(1.0, overlap / smaller_width)


def width_similarity(left: TableBoundary, right: TableBoundary) -> float:
    larger_width = max(left.width, right.width)
    if larger_width <= 0:
        return 0.0
    return min(left.width, right.width) / larger_width


def header_similarity(left: TableBoundary, right: TableBoundary) -> float:
    if not left.headers or not right.headers:
        return 0.0
    return text_similarity(" | ".join(left.headers), " | ".join(right.headers))


def boundary_proximity(left: TableBoundary, right: TableBoundary) -> float:
    bottom_score = max(
        0.0,
        min(1.0, (left.bottom - BOTTOM_BOUNDARY) / (1.0 - BOTTOM_BOUNDARY)),
    )
    top_score = max(0.0, min(1.0, (TOP_BOUNDARY - right.top) / TOP_BOUNDARY))
    return (bottom_score + top_score) / 2


def continuation_score(left: TableBoundary, right: TableBoundary) -> float:
    same_columns = float(left.num_cols > 0 and left.num_cols == right.num_cols)
    caption_similarity = text_similarity(left.caption, right.caption)
    return (
        0.25 * same_columns
        + 0.20 * horizontal_overlap(left, right)
        + 0.10 * width_similarity(left, right)
        + 0.20 * header_similarity(left, right)
        + 0.10 * caption_similarity
        + 0.15 * boundary_proximity(left, right)
    )


def detect_table_continuations(
    previous: PageSummary, current: PageSummary
) -> list[Continuation]:
    candidates = []
    for previous_index, previous_table in enumerate(previous.bottom_tables):
        for current_index, current_table in enumerate(current.top_tables):
            overlap = horizontal_overlap(previous_table, current_table)
            same_columns = (
                previous_table.num_cols > 0
                and previous_table.num_cols == current_table.num_cols
            )
            caption_match = text_similarity(
                previous_table.caption, current_table.caption
            )
            explicit = (
                previous_table.has_explicit_continuation
                or current_table.has_explicit_continuation
            )
            confirmed = explicit and overlap >= 0.50 and (
                same_columns or caption_match >= 0.60
            )
            score = 1.0 if confirmed else continuation_score(
                previous_table, current_table
            )
            if confirmed or score >= CONTINUATION_THRESHOLD:
                candidates.append(
                    (
                        score,
                        previous_index,
                        current_index,
                        Continuation(
                            previous_table=previous_table,
                            next_table=current_table,
                            confidence=score,
                            confirmed=confirmed,
                        ),
                    )
                )

    continuations = []
    used_previous = set()
    used_current = set()
    for _, previous_index, current_index, continuation in sorted(
        candidates, key=lambda candidate: candidate[0], reverse=True
    ):
        if previous_index in used_previous or current_index in used_current:
            continue
        continuations.append(continuation)
        used_previous.add(previous_index)
        used_current.add(current_index)
    return continuations


def navigation_markdown(
    page_number: int, page_count: int, pdf_stem: str, digits: int
) -> str:
    links = []
    if page_number > 1:
        previous_name = f"{pdf_stem}-page-{page_number - 1:0{digits}d}.md"
        links.append(f"[← Page {page_number - 1}]({previous_name})")
    links.append(f"Page {page_number} of {page_count}")
    if page_number < page_count:
        next_name = f"{pdf_stem}-page-{page_number + 1:0{digits}d}.md"
        links.append(f"[Page {page_number + 1} →]({next_name})")
    return " · ".join(links)


def wrap_page_markdown(markdown: str, navigation: str) -> str:
    return (
        f"{navigation}\n\n"
        f"{CONTINUATION_FROM_MARKER}\n\n"
        f"{markdown.rstrip()}\n\n"
        f"{CONTINUATION_TO_MARKER}\n\n"
        f"{navigation}\n"
    )


def continuation_label(continuation: Continuation) -> str:
    return "Continued" if continuation.confirmed else "Likely continuation"


def outgoing_continuation_markdown(
    continuations: Sequence[Continuation], target_page: int, target_href: str
) -> str:
    return "\n\n".join(
        f"> **{continuation_label(continuation)}:** A table on this page "
        f"continues on [page {target_page}]({target_href})."
        for continuation in continuations
    )


def incoming_continuation_markdown(
    continuations: Sequence[Continuation], source_page: int, source_href: str
) -> str:
    return "\n\n".join(
        f"> **{continuation_label(continuation)}:** A table from "
        f"[page {source_page}]({source_href}) continues on this page."
        for continuation in continuations
    )


def annotate_page_continuations(
    previous: PageSummary,
    current: PageSummary,
    previous_path: Path,
    current_path: Path,
    continuations: list[Continuation],
) -> None:
    if not continuations:
        return

    replace_annotation_marker(
        previous_path,
        CONTINUATION_TO_MARKER,
        outgoing_continuation_markdown(
            continuations, current.page_number, current_path.name
        ),
    )
    replace_annotation_marker(
        current_path,
        CONTINUATION_FROM_MARKER,
        incoming_continuation_markdown(
            continuations, previous.page_number, previous_path.name
        ),
    )


def replace_annotation_marker(path: Path, marker: str, annotation: str) -> None:
    markdown = path.read_text(encoding="utf-8")
    if marker not in markdown:
        raise ValueError(f"Continuation marker missing from {path}")
    start, end = marker.split("\n")
    replacement = f"{start}\n{annotation}\n{end}"
    path.write_text(markdown.replace(marker, replacement, 1), encoding="utf-8")


def document_navigation_markdown(page_number: int, page_count: int) -> str:
    links = []
    if page_number > 1:
        links.append(f"[← Page {page_number - 1}](#page-{page_number - 1})")
    links.append(f"Page {page_number} of {page_count}")
    if page_number < page_count:
        links.append(f"[Page {page_number + 1} →](#page-{page_number + 1})")
    return " · ".join(links)


def render_document_page(
    pending: PendingDocumentPage,
    outgoing_continuations: Sequence[Continuation],
    page_count: int,
) -> str:
    page_number = pending.page_number
    sections = [
        f'<a id="page-{page_number}"></a>',
        document_navigation_markdown(page_number, page_count),
    ]
    if pending.incoming_continuations:
        sections.append(
            incoming_continuation_markdown(
                pending.incoming_continuations,
                page_number - 1,
                f"#page-{page_number - 1}",
            )
        )
    if pending.markdown.strip():
        sections.append(pending.markdown.strip())
    if outgoing_continuations:
        sections.append(
            outgoing_continuation_markdown(
                outgoing_continuations,
                page_number + 1,
                f"#page-{page_number + 1}",
            )
        )
    sections.append(document_navigation_markdown(page_number, page_count))
    if page_number < page_count:
        sections.append("<!-- PDF page break -->")
    return "\n\n".join(sections) + "\n\n"


def export_page_markdown(
    document: DoclingDocument, markdown_path: Path, artifacts_dir: Path
) -> str:
    document.save_as_markdown(
        markdown_path,
        artifacts_dir=artifacts_dir,
        image_mode=ImageRefMode.REFERENCED,
    )
    return markdown_path.read_text(encoding="utf-8")


def convert_pdf(
    pdf_path: Path,
    output_dir: Path,
    dpi: int,
    model_path: str,
    output_mode: str,
) -> ConversionResult:
    if output_mode not in {"pages", "document"}:
        raise ValueError(f"Unsupported output mode: {output_mode}")

    output_dir.mkdir(parents=True, exist_ok=True)
    document_path = output_dir / f"{pdf_path.stem}.md"
    document_temp_path = output_dir / f".{pdf_path.stem}.md.tmp"
    document_published = False
    pdf = None
    try:
        if output_mode == "document":
            document_temp_path.write_text("", encoding="utf-8")

        pdf = pdfium.PdfDocument(pdf_path)
        page_count = len(pdf)
        if page_count == 0:
            raise ValueError("The PDF contains no pages.")
        digits = max(4, len(str(page_count)))

        print(f"Loading model {model_path}...")
        model, processor = load(model_path)
        config = load_config(model_path)
        make_detokenizer_utf8_safe(processor)
        formatted_prompt = apply_chat_template(
            processor, config, PROMPT, num_images=1
        )

        previous_summary = None
        previous_markdown_path = None
        pending_document_page = None
        for page_index in range(page_count):
            page_number = page_index + 1
            page = pdf[page_index]
            try:
                page_image = page.render(scale=dpi / 72).to_pil().convert("RGB")
            finally:
                page.close()

            output = generate_doctags(
                model,
                processor,
                formatted_prompt,
                page_image,
                page_number,
                page_count,
            )
            doctags_doc = DocTagsDocument.from_doctags_and_image_pairs(
                [output], [page_image]
            )
            document = DoclingDocument.load_from_doctags(
                doctags_doc,
                document_name=f"{pdf_path.stem}-page-{page_number}",
            )
            current_summary = summarize_page(document, page_number)
            artifacts_dir = Path("figures") / f"page-{page_number:0{digits}d}"

            if output_mode == "pages":
                markdown_path = page_markdown_path(
                    pdf_path, output_dir, page_number, digits
                )
                markdown = export_page_markdown(
                    document, markdown_path, artifacts_dir
                )
                navigation = navigation_markdown(
                    page_number, page_count, pdf_path.stem, digits
                )
                markdown_path.write_text(
                    wrap_page_markdown(markdown, navigation), encoding="utf-8"
                )

                if previous_summary is not None:
                    assert previous_markdown_path is not None
                    continuations = detect_table_continuations(
                        previous_summary, current_summary
                    )
                    annotate_page_continuations(
                        previous_summary,
                        current_summary,
                        previous_markdown_path,
                        markdown_path,
                        continuations,
                    )
                previous_summary = current_summary
                previous_markdown_path = markdown_path
            else:
                page_temp_path = output_dir / (
                    f".{pdf_path.stem}-page-{page_number:0{digits}d}.export.tmp.md"
                )
                try:
                    markdown = export_page_markdown(
                        document, page_temp_path, artifacts_dir
                    )
                finally:
                    page_temp_path.unlink(missing_ok=True)

                current_pending_page = PendingDocumentPage(
                    page_number=page_number,
                    markdown=markdown,
                    summary=current_summary,
                )
                if pending_document_page is not None:
                    continuations = detect_table_continuations(
                        pending_document_page.summary, current_summary
                    )
                    current_pending_page = PendingDocumentPage(
                        page_number=page_number,
                        markdown=markdown,
                        summary=current_summary,
                        incoming_continuations=tuple(continuations),
                    )
                    with document_temp_path.open("a", encoding="utf-8") as fp:
                        fp.write(
                            render_document_page(
                                pending_document_page,
                                continuations,
                                page_count,
                            )
                        )
                pending_document_page = current_pending_page

            del document, doctags_doc, output, page_image
            gc.collect()
            mx.clear_cache()

        if output_mode == "document":
            assert pending_document_page is not None
            with document_temp_path.open("a", encoding="utf-8") as fp:
                fp.write(
                    render_document_page(pending_document_page, (), page_count)
                )
            document_temp_path.replace(document_path)
            document_published = True
    finally:
        if pdf is not None:
            pdf.close()
        if output_mode == "document" and not document_published:
            document_temp_path.unlink(missing_ok=True)

    markdown_destination = document_path if output_mode == "document" else output_dir
    return ConversionResult(
        page_count=page_count,
        output_mode=output_mode,
        markdown_destination=markdown_destination,
    )


def main() -> None:
    args = parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"error: PDF not found: {args.pdf}")
    if args.pdf.suffix.lower() != ".pdf":
        raise SystemExit(f"error: input is not a PDF file: {args.pdf}")
    if args.dpi <= 0:
        raise SystemExit("error: --dpi must be greater than zero")

    result = convert_pdf(
        pdf_path=args.pdf,
        output_dir=args.output_dir,
        dpi=args.dpi,
        model_path=args.model,
        output_mode=args.output_mode,
    )
    if result.output_mode == "pages":
        print(
            f"Saved {result.page_count} Markdown page(s) to: "
            f"{result.markdown_destination}"
        )
    else:
        print(f"Markdown saved to: {result.markdown_destination}")
    print(f"Figures saved to: {args.output_dir / 'figures'}")


if __name__ == "__main__":
    main()
