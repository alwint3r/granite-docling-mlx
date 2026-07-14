# Add selectable per-page and whole-document Markdown output modes

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This repository does not contain its own `PLANS.md`; this plan follows the user-level guidance at `/Users/alwin/.agents/skills/exec-planner/references/PLANS.md`, but all implementation knowledge needed for the task is repeated here.

## Purpose / Big Picture

After this change, a user can choose whether a PDF produces one Markdown file per PDF page or one Markdown file for the complete PDF. The choice will be explicit at the command line, documented in `README.md`, and covered by tests. Existing per-page continuation links and bounded page-image memory usage must remain available in page mode. Whole-document mode must produce one predictable `<pdf-stem>.md` file and clearly define how page boundaries and detected table continuations appear inside that file.

A user will observe the feature by running the CLI twice against the same PDF with different output directories and output modes. Page mode will create files such as `report-page-0001.md`; document mode will create only `report.md` as its Markdown output.

## Progress

- [x] (2026-07-14 10:13Z) Created the initial ExecPlan after checking the active branch and identifying `main.py`, `README.md`, and `tests/test_main.py` as the likely touch points.
- [x] (2026-07-14 10:18Z) Confirmed the current conversion and annotation flow and verified that whole-document mode can stream page exports while preserving referenced-image paths.
- [x] (2026-07-14 10:36Z) Added the CLI mode interface, decoupled structural summaries from paths, and implemented page-file and atomic streaming document-file conversion paths.
- [x] (2026-07-14 10:43Z) Added CLI, document rendering, dual-mode integration, atomic-failure, and figure-reference tests; all 16 tests pass.
- [x] (2026-07-14 10:48Z) Updated usage documentation and completed real-model, two-page smoke tests for both output modes with the expected file layouts and no temporary files left behind.
- [x] (2026-07-14 10:51Z) Recorded final outcomes and committed the implementation as `4f50640 feat: add selectable Markdown output modes`.

## Surprises & Discoveries

The current `PageSummary` stores a `markdown_path`, even though continuation detection itself only needs page number and table boundary metadata. Document mode has no per-page final path, so implementation should decouple structural summaries from output-file locations rather than inserting a fake path.

A focused artifact-export probe confirmed that a one-page `DoclingDocument` can be saved to a temporary Markdown file while images are written to their final page-specific directory. The generated Markdown reference is independent of the temporary Markdown filename:

    ![Image](figures/page-0001/image_000000_<hash>.png)

This makes a bounded-memory, single-file document assembler feasible without reconstructing one multi-page `DoclingDocument` or retaining every rendered image.

The dual-mode integration test can exercise the real PDFium rendering and Docling export paths without loading Granite by patching only model loading and DocTags generation. It completed both two-page modes in under one second while verifying exact Markdown file counts. This gives broader coverage than pure string-rendering tests without making the regular suite depend on model weights.

The real Granite smoke test produced exactly `document/smoke.md` in document mode and `pages/smoke-page-0001.md` plus `pages/smoke-page-0002.md` in page mode. The document contained both internal anchors and both recognized page texts, and a search found no `.*tmp*` files after either run.

## Decision Log

- Decision: Expose one option named `--output-mode` with values `pages` and `document`.
  Rationale: The name describes what changes, while constrained values give users useful `argparse` validation and leave room for future output layouts without combining several Boolean flags.
  Date/Author: 2026-07-14 / coding agent.

- Decision: Default to `document` mode unless deeper repository inspection reveals a compatibility reason not to do so.
  Rationale: The original released CLI produced one whole-document Markdown file. Keeping that behavior when the new flag is omitted avoids silently breaking existing scripts; page output remains available through `--output-mode pages`.
  Date/Author: 2026-07-14 / coding agent.

- Decision: Keep continuation inference restricted to explicit continuation labels and high-confidence table matching in this change.
  Rationale: Paragraph and list continuation detection are separate accuracy features and should not be mixed into a mode-selection change.
  Date/Author: 2026-07-14 / coding agent.

- Decision: Implement document mode as a streaming assembly of one-page Markdown exports, not by restoring the former all-pages-in-memory `DoclingDocument` construction.
  Rationale: A probe showed that temporary page Markdown exports can point at final page-specific figure directories. Streaming preserves bounded page-image memory in both modes and directly addresses the original large-PDF concern. The resulting `report.md` is one document for users, even though it is assembled from page-level Docling conversions internally.
  Date/Author: 2026-07-14 / coding agent.

- Decision: Publish document mode atomically through an output-directory temporary file, replacing `<stem>.md` only after the final page is assembled.
  Rationale: Whole-document users should not mistake a failed partial conversion for a complete document. Page mode remains incrementally visible; document mode keeps the previous complete file intact if a rerun fails.
  Date/Author: 2026-07-14 / coding agent.

## Outcomes & Retrospective

The implementation provides both requested layouts behind `--output-mode`, with `document` as the backward-compatible default. Both paths share one page-at-a-time Granite inference loop. Page mode preserves immediate files, file navigation, and continuation annotations; document mode keeps one pending Markdown page, emits internal anchors and continuation links, and atomically publishes one final file. Sixteen tests pass, including dual-mode conversion, figure-reference placement, CLI validation, and preservation of an existing document when a replacement conversion fails. A real-model two-page smoke test passed in both modes. The code, tests, and user documentation were committed as `4f50640`; no implementation gaps remain in this plan.

## Context and Orientation

The project is a Python CLI in repository root `main.py`. It renders PDF pages through `pypdfium2`, sends each rendered page to the IBM Granite Docling MLX model, converts generated DocTags into `DoclingDocument` objects, and exports Markdown through `docling-core`. A DocTag is the model’s structured text representation of one page. The current branch processes one page at a time and writes `<stem>-page-NNNN.md`; it retains only a lightweight `PageSummary` from the previous page to detect tables that may continue across a boundary.

`main.py` currently owns argument parsing, model inference, continuation scoring, Markdown navigation, and output writing. `README.md` describes the current per-page behavior. `tests/test_main.py` exercises continuation scoring, page summaries, and Markdown annotation helpers using Python’s standard `unittest` framework. `pyproject.toml` contains runtime dependencies; no new dependency should be needed.

“Page mode” in this plan means one Markdown file for every PDF page. “Document mode” means one Markdown file for the entire PDF. “Bounded page-image memory” means that the number of rendered page images retained at once does not grow with PDF page count; the loaded MLX model remains resident in either mode.

Document mode can preserve bounded page-image memory while producing a single file. Serialize each one-page `DoclingDocument` to a temporary Markdown file located in the output directory, with figures sent directly to `figures/page-NNNN/`. Read and delete that temporary Markdown file, retain at most one page’s Markdown and summary for look-ahead, then append the finalized page section to a temporary whole-document file. After the last page, atomically replace `<stem>.md` with the completed temporary document. Because the temporary page export and final document share a parent directory, relative figure references remain correct.

## Plan of Work

The initial inspection is complete. `convert_pdf` currently contains one model-loading and page-inference loop, then immediately writes and wraps each page file. `PageSummary` mixes structural metadata with `markdown_path`, and `annotate_continuations` derives links from those paths. Before adding the mode branch, remove `markdown_path` from `PageSummary`; pass actual paths to page-file annotation helpers, and generate document-mode anchor links separately. This keeps `detect_table_continuations` independent of output layout.

Next, extend `parse_args` with `--output-mode`, constrained by `choices=("pages", "document")` and defaulting to `document`. Introduce a small mode value convention rather than a new class hierarchy: this repository has only two concrete paths and does not need an output-strategy framework. Thread the selected mode from `main` into conversion.

Refactor the existing page inference loop only enough to share expensive work. The loop should continue to render exactly one page, generate DocTags, create one one-page `DoclingDocument`, save any figures, compute a `PageSummary`, and release page objects before advancing. Avoid duplicating model loading and Granite generation in separate top-level conversion functions.

For page mode, preserve the current behavior: write `<stem>-page-NNNN.md`, include previous/next file links at the top and bottom, store figures under `figures/page-NNNN/`, and annotate adjacent files after the next page summary becomes available. The output summary printed by `main` must state how many page files were saved.

For document mode, write `<stem>.md` through `<output-dir>/.<stem>.md.tmp`. Give every page a stable HTML anchor such as `<a id="page-1"></a>` and separate pages with `<!-- PDF page break -->`. Add internal previous/next links using `#page-N` anchors rather than file links. When a continuation is detected, place an outgoing note after the earlier page and an incoming note before the later page, also using internal anchors. Save figures in page-specific artifact directories so repeated image indexes cannot collide.

Use a one-page look-ahead. A pending page record contains its page number, Markdown text, structural `PageSummary`, and incoming continuation list. When the next page arrives, compare summaries, finalize the pending page with outgoing notes, append it to the temporary whole-document file, and retain the current page as the new pending page. At end-of-file, append the final pending page and atomically replace `<stem>.md`. Use a temporary per-page Markdown export only to make `save_as_markdown` write figures and produce referenced Markdown; read it immediately and remove it in a `finally` block. The operation must not leave temporary Markdown files after success or failure.

Finally, update `tests/test_main.py` with mode parsing or conversion orchestration tests that mock model inference, plus pure tests for document assembly and links. Update `README.md` with both commands, defaults, filenames, navigation behavior, continuation behavior, and memory characteristics. Run unit tests, syntax checks, whitespace checks, and two end-to-end smoke conversions.

## Concrete Steps

All commands run from the repository root `/Users/alwin/gitrepo/self/granite-docling-cli`.

1. Inspect the current mode-relevant code and tests:

       git status --short --branch
       rg -n "def (parse_args|convert_pdf|wrap_page_markdown|annotate_continuations|main)" main.py
       uv run python -m unittest discover -s tests -v

   Expect the branch name `feature/per-page-streaming-continuations`, a clean implementation tree apart from this plan artifact, and all existing tests to pass.

2. Modify `main.py` to add `--output-mode {pages,document}` with `document` as the default. Keep a single model-loading and page-inference loop, but route each converted page into either the existing page-file writer or a new document-file assembler.

3. Modify `tests/test_main.py`. Add tests proving that omitted mode means `document`, explicit `pages` is accepted, invalid values are rejected by `argparse`, page mode retains file links, and document mode uses internal page anchors and emits exactly one Markdown path. Mock or isolate MLX generation so the regular test suite does not download or run the model.

4. Modify `README.md` to include commands equivalent to:

       granite-docling input.pdf --output-dir output
       granite-docling input.pdf --output-dir output --output-mode pages

   State that the first command creates `output/input.md`, while the second creates `output/input-page-0001.md`, `output/input-page-0002.md`, and so on.

5. Run validation:

       uv run python -m unittest discover -s tests -v
       uv run python -m py_compile main.py tests/test_main.py
       git diff --check

   Expect every test to report `ok`, compilation to produce no output, and `git diff --check` to produce no output.

6. Create a small two-page PDF and run both modes with the cached Granite model. Use separate output directories, then inspect files:

       uv run main.py /tmp/granite-mode-smoke/smoke.pdf -o /tmp/granite-mode-smoke/document
       uv run main.py /tmp/granite-mode-smoke/smoke.pdf -o /tmp/granite-mode-smoke/pages --output-mode pages
       find /tmp/granite-mode-smoke/document -maxdepth 3 -type f | sort
       find /tmp/granite-mode-smoke/pages -maxdepth 3 -type f | sort

   Expect exactly one Markdown file in the document directory and two Markdown files in the pages directory. Both conversions should contain text from both source pages in the appropriate output files.

7. Update this plan’s living sections, review `git diff`, and commit the code and tests on the active feature branch.

## Validation and Acceptance

Acceptance is user-visible. Running without `--output-mode` must produce `output/<stem>.md` and no `<stem>-page-*.md` files created by that run. That file must contain page 1 followed by page 2, stable page anchors, and referenced figures that resolve relative to the Markdown file.

Running with `--output-mode pages` must produce one Markdown file per source page. Each page file must link to adjacent page files where applicable. Existing explicit and likely table continuation notes must remain present when the corresponding detector returns a match.

Both modes must process page images incrementally: an implementation test or code-level test should prove the conversion loop does not first construct a list of all rendered images. For document mode, no list of all `DoclingDocument` objects or all page images may be retained. Holding one pending page’s Markdown string and summary is acceptable.

`uv run python -m unittest discover -s tests -v` must report all 16 tests as passing. `uv run python -m py_compile main.py tests/test_main.py` and `git diff --check` must be silent. A two-page real-model smoke test must create the expected file counts and readable links. CLI help must show:

       --output-mode {pages,document}

An invalid value such as `--output-mode pdf` must exit nonzero with an `argparse` invalid-choice message.

## Idempotence and Recovery

The implementation should truncate or overwrite the selected Markdown outputs at the start of a run, so repeating the same command does not duplicate document sections or continuation notes. Page-mode annotation markers must still be replaced at most once per run. Document-mode temporary files, if needed, must use deterministic page-specific names or `tempfile` and be removed in a `finally` block.

Do not delete unrelated files from the user’s output directory. A rerun may overwrite files for the same PDF stem and selected mode, but it must not broadly clean the directory. If conversion fails halfway, already completed page-mode files may remain useful. Document mode must write to `.<stem>.md.tmp` and call `Path.replace` only after the final pending page is appended. On failure, remove that temporary file and leave any pre-existing `<stem>.md` untouched. Rerunning must truncate and rebuild the temporary file from the beginning.

To abandon the implementation while keeping this plan, restore code files with `git restore main.py README.md tests/test_main.py`. To return to the committed branch state including removal of untracked implementation artifacts, inspect `git status` first and remove only files created by the failed attempt.

## Artifacts and Notes

Current relevant branch and commit at plan creation:

    branch: feature/per-page-streaming-continuations
    commit: ea3efd0 feat: stream per-page markdown with continuation links

Expected page-mode layout:

    output/
      report-page-0001.md
      report-page-0002.md
      figures/page-0001/...
      figures/page-0002/...

Expected document-mode layout:

    output/
      report.md
      figures/page-0001/...
      figures/page-0002/...

The final document-mode boundary is recognizable and linkable:

    <!-- PDF page break -->

    <a id="page-2"></a>

Final validation evidence:

    Ran 16 tests in 0.540s
    OK

    document files: document/smoke.md
    page files: pages/smoke-page-0001.md, pages/smoke-page-0002.md
    temporary files after success: none

## Interfaces and Dependencies

No dependency changes are planned. Continue using `argparse`, `pathlib.Path`, `pypdfium2`, `mlx`, `mlx_vlm`, and `docling_core` already declared or transitively available through `pyproject.toml`.

At completion, `main.py` must expose these effective interfaces, though helper names can be adjusted if tests and this plan are updated together:

    parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace

Passing `argv` makes CLI behavior testable without mutating process-global arguments; `None` retains normal command-line behavior. The returned namespace has `output_mode` equal to either `"pages"` or `"document"`.

    convert_pdf(
        pdf_path: Path,
        output_dir: Path,
        dpi: int,
        model_path: str,
        output_mode: str,
    ) -> ConversionResult

Define `ConversionResult` as a small dataclass with `page_count: int`, `output_mode: str`, and `markdown_destination: Path`. In document mode, `markdown_destination` is `<output-dir>/<stem>.md`; in page mode it is `output_dir`. This lets `main` print mode-appropriate output without rebuilding naming rules. Do not add a reusable strategy hierarchy; two small concrete writer paths and shared helper functions are sufficient.

Refactor `PageSummary` to contain only `page_number`, `top_tables`, and `bottom_tables`; it must not contain a filesystem path. Document-mode assembly needs a small `PendingDocumentPage` record containing `page_number`, `markdown`, `summary`, and incoming continuations, plus a helper that accepts that record and outgoing continuations and returns one finalized page section. Page mode may continue using `wrap_page_markdown`, but its annotation helper must receive previous/current paths explicitly. Continuation detection remains `detect_table_continuations(previous, current) -> list[Continuation]` and must not become mode-dependent.

Change note, 2026-07-14 10:13Z: Created the initial plan. The document-mode streaming details remain subject to verification against current artifact export behavior before implementation.

Change note, 2026-07-14 10:18Z: Verified temporary Markdown artifact paths, selected streaming and atomic document assembly, and added the required `PageSummary` decoupling so both output modes can share continuation detection cleanly.

Change note, 2026-07-14 10:22Z: Removed recovery ambiguity after choosing atomic document publication and made the testable argument-parser, conversion-result, and pending-page interfaces explicit.

Change note, 2026-07-14 10:36Z: Recorded completion of the initial CLI and dual-mode conversion implementation; tests and documentation remain in progress.

Change note, 2026-07-14 10:43Z: Recorded 15 passing tests, including real PDFium/Docling integration with model inference patched and atomic document failure recovery.

Change note, 2026-07-14 10:48Z: Recorded the figure-reference test, 16-test result, README update, and successful real-model smoke output for both modes.

Change note, 2026-07-14 10:51Z: Marked the plan complete after committing the implementation as `4f50640` and added final validation evidence.
