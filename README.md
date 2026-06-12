# gtEditor

PySide6-based ground-truth correction editor for cropped table PNGs and matching table-structure JSON records.

This repository is intentionally scoped to the GT editor only. It includes the editor, validation helpers, tests, and local `Input_data/` / `Output_data/` placeholders so the workflow can be cloned, reviewed, and run independently of the larger dataset-generation workspace.

## What gtEditor edits

A dataset is a folder with matched table crop images and JSON files:

```text
<input-dataset>/image/<table_id>.png
<input-dataset>/json/<table_id>.json
```

`json_formatted/` is also accepted as the JSON folder name:

```text
<input-dataset>/image/<table_id>.png
<input-dataset>/json_formatted/<table_id>.json
```

The image and JSON basenames must match. A parent folder may contain multiple such dataset folders; each discovered dataset opens as one GUI tab.

## Quick start

### One-click launcher

From the repository root:

```bash
./run_gt_editor.sh
```

The launcher:

1. creates `.venv/` if needed;
2. installs runtime dependencies with `pip install -e .` if needed;
3. discovers datasets under `Input_data/**/{image,json|json_formatted}`;
4. writes decisions to `Output_data/`.

Extra CLI flags can be appended:

```bash
./run_gt_editor.sh --limit 10
```

### Manual setup

```bash
git clone https://github.com/CHCHO67/gtEditor.git
cd gtEditor
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run tests/smoke checks:

```bash
uv run --with pytest python -m pytest tests/test_export_validate.py -q
QT_QPA_PLATFORM=offscreen uv run --with pytest python -m pytest tests/test_input_output_workflow.py::test_gui_tabs_and_save_actions_offscreen -q
```

Launch with explicit input/output folders:

```bash
PYTHONPATH=src python src/cli.py \
  --input-data Input_data/tagged_table_PDF_1k_TTEcrop_passed \
  --output-data Output_data
```

Repeat `--input-data` to open multiple top-level inputs:

```bash
PYTHONPATH=src python src/cli.py \
  --input-data /path/to/Input_data_a \
  --input-data /path/to/Input_data_b \
  --output-data /path/to/Output_data
```

Legacy single-folder flags remain available:

```bash
PYTHONPATH=src python src/cli.py \
  --image-dir <dataset>/image \
  --json-dir <dataset>/json \
  --export-dir Output_data
```

Headless utility commands:

```bash
PYTHONPATH=src python src/cli.py --headless-smoke --input-data <dataset-parent> --limit 3
PYTHONPATH=src python src/cli.py --input-data <dataset-parent> --output-data /tmp/gt_editor_output --save-all --limit 3
QT_QPA_PLATFORM=offscreen PYTHONPATH=src python src/cli.py --smoke-exit --input-data <dataset-parent> --output-data /tmp/gt_editor_output --limit 3
```

## GUI workflow

The GUI is organized around one reviewer decision per table crop.

- Top-level tabs: one per input dataset folder.
- Status sub-tabs:
  - **Needs Review**: not yet saved/discarded.
  - **Accepted Origin**: saved without structural edits, or treated as accepted original.
  - **Revision**: saved after structural edits or migrated from edited legacy output.
  - **Discard**: rejected sample.
- Header buttons:
  - **Discard** (`Ctrl+D`) writes the current pair to `discarded/`.
  - **Save** (`Ctrl+S`) writes to `accepted_origin/` or `revision/` depending on edit state.

Output layout:

```text
<output-data>/<tab-name>/accepted_origin/image/<table_id>.png
<output-data>/<tab-name>/accepted_origin/json/<table_id>.json
<output-data>/<tab-name>/revision/image/<table_id>.png
<output-data>/<tab-name>/revision/json/<table_id>.json
<output-data>/<tab-name>/discarded/image/<table_id>.png
<output-data>/<tab-name>/discarded/json/<table_id>.json
```

Older `saved/` output is readable for migration/status restoration, but new saves target only `accepted_origin/`, `revision/`, or `discarded/`.

## Editing controls

Default interaction is grid-line movement:

- click and drag a grid line, then release to commit the move;
- vertical grid lines are red;
- horizontal grid lines are blue;
- `Alt+Arrow` nudges a selected line;
- `Shift+Alt+Arrow` nudges faster;
- `D` deletes a selected line;
- `V` adds a vertical line at the cursor;
- `H` adds a horizontal line at the cursor;
- `Ctrl+Z` undoes.

Cell operations:

- **Select Cells** (`C`) enables drag selection across cells;
- in normal line-move mode, hold `Ctrl` while dragging cells for temporary cell selection;
- selected cells are translucent blue;
- `1` merges selected adjacent cells;
- merged cells are translucent purple;
- `2` unmerges one selected merged cell.

Text spans are drawn as green translucent boxes over the source crop/page preview without duplicating text labels.

## Saved JSON schema and integrity policy

gtEditor saves review JSON in the `table_structure_val/json`-compatible schema. The target reference is the val dataset format used by:

```text
/home/chanho-cho/projects/Datasets/table_structure_val/json
```

Canonical top-level key order:

```text
source_pdf, page_no, table_index, global_index, num_rows, num_cols,
image_size, table_bbox_px, h_lines, v_lines, cells, layout_tedss_score
```

Canonical cell key order:

```text
row, col, end_row, end_col, row_span, col_span, text,
is_column_header, is_row_header, is_row_section, is_fillable, bbox_px
```

Formatting policy:

- JSON is written with `json.dumps(..., ensure_ascii=False, indent=2)` semantics.
- There is **no trailing newline**.
- UTF-8 text is emitted directly, not escaped as `\uXXXX` unless required by JSON itself.
- Key order is stable and matches the val schema above.

Value/type policy:

| Field | Saved policy |
| --- | --- |
| `source_pdf` | string copied from the loaded record |
| `page_no`, `table_index`, `global_index`, `num_rows`, `num_cols` | integers |
| `image_size` | `[int, int]`, matching the saved PNG size |
| `table_bbox_px` | normalized to `[0.0, 0.0, float(image_w), float(image_h)]` for review output |
| `h_lines`, `v_lines` | dictionaries with string integer keys and float values, generated from current grid axes |
| `cells` | list of non-empty-text table cells in canonical cell-key order |
| `layout_tedss_score` | float; `None` is exported as `0.0` placeholder |
| `crop_status` | never emitted |

Cell policy:

- `row`, `col`, `end_row`, `end_col`, `row_span`, `col_span` remain integers.
- `text` remains a string.
- Cells with `text == ""` are omitted from saved review JSON. This matches the cleaned Output_data integrity contract and avoids val-incompatible placeholder cells from source crops.
- Header/fillable flags remain booleans.
- `bbox_px` is exported as four floats from the current grid geometry.
- Exact duplicate logical cells with the same grid key and same text are collapsed; boolean flags are OR-merged. Same grid span with different text is preserved as a genuine grid collision.

Coordinate policy:

- `cells[].bbox_px`, `h_lines`, and `v_lines` are crop-relative coordinates.
- Saved review `table_bbox_px` is also normalized to the crop extent so the bundled validator can enforce `table_bbox_px` width/height against `image_size`.
- Project-state export (`.gt.json`-style state) may preserve source metadata separately; review output JSON prioritizes downstream val compatibility.

## Validation guarantees

Every GUI/CLI save calls `save_output_pair()`, which:

1. copies the PNG to the matching bucket `image/` folder;
2. writes canonical review JSON to the bucket `json/` folder;
3. runs the bundled Docling/PNG validator;
4. raises a save error if validation fails.

The repository also carries regression tests for the val-clean export contract:

```bash
uv run --with pytest python -m pytest tests/test_export_validate.py -q
```

For a classified `Output_data/`, the stronger integrity check used during dataset handoff is:

```bash
uv run python /tmp/verify_output_integrity.py
```

That checker verifies:

- val top-level key order;
- val cell key order;
- field types;
- `layout_tedss_score` is float;
- `crop_status` is absent;
- canonical byte formatting/no trailing newline;
- image size matches PNG;
- cell row/column bounds;
- bundled gtEditor validator passes;
- each input stem has exactly one output classification;
- no extra/missing/duplicate stems;
- no empty string `text` cells.

## Repository layout

The project uses a flat `src/` module layout:

- `src/cli.py` — CLI and GUI entry point.
- `src/app.py` — PySide6 main window.
- `src/graphics_scene.py` — table/cell/grid scene rendering.
- `src/models.py` — in-memory table model.
- `src/commands.py` — undoable grid/cell operations.
- `src/io_docling.py` — load/export/validate table JSON records.
- `src/text_assign.py` — text assignment after grid edits.
- `src/docling_validator.py` — bundled val-style schema/PNG validator used by tests and exports.
- `tests/` — regression and GUI/offscreen smoke tests.

## Current status

gtEditor can load paired PNG/JSON datasets, inspect and edit table grids/cells, classify each table as accepted origin/revision/discarded, save val-compatible review JSON, and validate exported image/JSON pairs automatically.
