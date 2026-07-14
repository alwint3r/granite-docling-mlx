# granite-docling

Convert PDFs to Docling-flavoured Markdown with IBM Granite Docling running through MLX.

## Build the application bundle

The project uses a PyInstaller **onedir** build. It starts much faster than a
one-file build because its dependencies remain next to the executable instead
of being extracted every time it runs. Build on the same operating system and
CPU architecture on which the application will run (MLX requires Apple
Silicon/macOS):

```bash
uv sync --group dev
uv run pyinstaller --noconfirm --clean granite-docling.spec
```

This creates the self-contained application bundle at `dist/granite-docling/`.
Keep the directory and all of its contents together; the executable is
`dist/granite-docling/granite-docling`.

## Install

Copy the bundle to a permanent location, then symlink its executable into a
directory on your `PATH`:

```bash
mkdir -p "$HOME/.local/lib" "$HOME/.local/bin"
rm -rf "$HOME/.local/lib/granite-docling"
ditto dist/granite-docling "$HOME/.local/lib/granite-docling"
ln -sf "$HOME/.local/lib/granite-docling/granite-docling" "$HOME/.local/bin/granite-docling"
```

If `~/.local/bin` is not already on your `PATH`, add this to your shell profile
(such as `~/.zshrc`), then restart the shell:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

`ditto` preserves the bundle's internal symbolic links, which MLX requires. The
PATH symlink lets you run the application from anywhere while PyInstaller still
locates its bundled libraries beside the real executable. Do not copy or move
only `granite-docling`; move the full bundle directory instead.

## Usage

By default, the converter writes the entire PDF to one Markdown file:

```bash
granite-docling input.pdf --output-dir output
# creates output/input.md
```

To write one Markdown file per PDF page, select `pages` mode:

```bash
granite-docling input.pdf --output-dir output --output-mode pages
# creates output/input-page-0001.md, output/input-page-0002.md, ...
```

The available modes are:

- `document` (default) assembles one `input.md` file with internal page anchors
  and previous/next page links. The complete file is published atomically after
  conversion finishes.
- `pages` saves each completed page immediately and links it to adjacent page
  files.

In both modes, extracted figures are grouped by source page under directories
such as `output/figures/page-0001/`. The converter compares tables at adjacent
page boundaries. A repeated, aligned table receives a **Likely continuation**
link; an explicit source label such as “Table 4 (continued)” receives a
**Continued** link. These annotations are navigational only: tables are not
merged.

Both modes retain only the model, the current rendered page, one pending page's
Markdown, and lightweight boundary metadata. Rendered pages therefore do not
accumulate in memory as the PDF grows.

Use `--model` to select previously downloaded local model weights:

```bash
granite-docling input.pdf --output-dir output \
  --model /path/to/granite-docling-258M-mlx
```

The bundle includes the Python runtime and application dependencies, but not
the Granite model weights. The default model downloads from Hugging Face into
the user's cache on first use.

## Development

```bash
uv run main.py input.pdf --output-dir output
uv run python -m unittest discover -s tests
```
