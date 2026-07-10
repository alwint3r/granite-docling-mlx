import argparse
from pathlib import Path

import pypdfium2 as pdfium
from docling_core.types.doc import ImageRefMode
from docling_core.types.doc.document import DocTagsDocument, DoclingDocument
from mlx_vlm import load, stream_generate
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config

DEFAULT_MODEL = "ibm-granite/granite-docling-258M-mlx"
PROMPT = "Convert this page to docling."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a PDF to Markdown and extract detected figures."
    )
    parser.add_argument("pdf", type=Path, help="PDF file to process")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for the Markdown file and extracted figures",
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
    return parser.parse_args()


def render_pdf(pdf_path: Path, dpi: int) -> list:
    print(f"Rendering {pdf_path} at {dpi} DPI...")
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        images = [
            page.render(scale=dpi / 72).to_pil().convert("RGB") for page in pdf
        ]
    finally:
        pdf.close()

    if not images:
        raise ValueError("The PDF contains no pages.")
    return images


def generate_doctags(model, processor, config, page_images: list) -> list[str]:
    formatted_prompt = apply_chat_template(
        processor, config, PROMPT, num_images=1
    )
    outputs = []

    for page_number, page_image in enumerate(page_images, start=1):
        print(f"Processing page {page_number}/{len(page_images)}...")
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
        outputs.append(output)

    return outputs


def convert_pdf(pdf_path: Path, output_dir: Path, dpi: int, model_path: str) -> Path:
    page_images = render_pdf(pdf_path, dpi)

    print(f"Loading model {model_path}...")
    model, processor = load(model_path)
    config = load_config(model_path)

    outputs = generate_doctags(model, processor, config, page_images)
    doctags_doc = DocTagsDocument.from_doctags_and_image_pairs(
        outputs, page_images
    )
    document = DoclingDocument.load_from_doctags(
        doctags_doc, document_name=pdf_path.stem
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / f"{pdf_path.stem}.md"
    document.save_as_markdown(
        markdown_path,
        artifacts_dir=Path("figures"),
        image_mode=ImageRefMode.REFERENCED,
    )
    return markdown_path


def main() -> None:
    args = parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"error: PDF not found: {args.pdf}")
    if args.pdf.suffix.lower() != ".pdf":
        raise SystemExit(f"error: input is not a PDF file: {args.pdf}")
    if args.dpi <= 0:
        raise SystemExit("error: --dpi must be greater than zero")

    markdown_path = convert_pdf(
        pdf_path=args.pdf,
        output_dir=args.output_dir,
        dpi=args.dpi,
        model_path=args.model,
    )
    print(f"Markdown saved to: {markdown_path}")
    print(f"Figures saved to: {args.output_dir / 'figures'}")


if __name__ == "__main__":
    main()
