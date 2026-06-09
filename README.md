# gtEditor

PySide6-based ground-truth correction editor for cropped table PNGs and matching Docling-compatible JSON records.

This repository is intentionally scoped to the GT editor only. It includes a small portable sample set, tests, and smoke scripts so it can be cloned and run on another machine without the larger Robin_TTE workspace.

## What it edits

Input layout:

```text
<dataset>/image/<table_id>.png
<dataset>/json/<table_id>.json
```

The PNG and JSON basenames must match. The JSON is expected to be a Docling-style table record with fields such as `num_rows`, `num_cols`, `h_lines`, `v_lines`, `cells`, and `layout_tedss_score`.

## Quick start

```bash
git clone https://github.com/CHCHO67/gtEditor.git
cd gtEditor
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

Launch the GUI with one or more Input_data folders and an Output_data destination:

```bash
gt-editor \
  --input-data gt_editor_samples \
  --output-data gt_editor_outputs
```

Repeat `--input-data` to open multiple GUI tabs. Each input folder must contain matching `image/` and `json/` children. The GUI is organized as:

```text
Input_data tab
  검토        # not saved or discarded yet
  검토 완료   # moved here after Save
  버리기      # moved here after Discard
```

Only the current file has visible decision buttons in the upper-right header: **Discard** (orange, `Ctrl+D`) and **Save** (green, `Ctrl+S`). Both actions write the current image/JSON pair to Output_data and validate the exported JSON. Output saves use the same structure under a per-tab folder:

```text
<output-data>/<tab-name>/image/<table_id>.png
<output-data>/<tab-name>/json/<table_id>.json
```

Editing tools are available as both buttons and shortcuts. The default interaction after selecting a new image is **line movement**: click a grid line, drag it freely, then release it at the desired position. Add V/H creates a line at the mouse cursor position. Use **Select Cells** (`C`) to rubber-band or click adjacent cells, then **Merge** (`M`); merged cells are shown with a translucent purple fill. Select a merged cell and press **Unmerge** (`U`) to split it again. Other shortcuts remain: `Alt+Arrow` nudges a selected line, `Del` deletes a selected line, and `Ctrl+Z` undoes. Tab names derived from duplicate folder names are disambiguated automatically.

The legacy single-folder flags remain available:

```bash
gt-editor \
  --image-dir gt_editor_samples/image \
  --json-dir gt_editor_samples/json \
  --export-dir gt_editor_exports
```

Headless smoke and save-all checks:

```bash
gt-editor --headless-smoke --input-data gt_editor_samples --limit 3
gt-editor --input-data gt_editor_samples --output-data /tmp/gt_editor_output --save-all --limit 3
python scripts/smoke_gt_editor.py --out-dir /tmp/gt_editor_smoke
```

GUI smoke check for headless/CI environments:

```bash
QT_QPA_PLATFORM=offscreen gt-editor --smoke-exit --input-data gt_editor_samples --output-data /tmp/gt_editor_output --limit 3
```

## Included sample data

`gt_editor_samples/` contains 10 small PNG/JSON pairs copied as real files, not symlinks. These are enough to run tests and smoke checks after cloning.

## Main modules

- `gt_editor/cli.py` — CLI and GUI entry point.
- `gt_editor/app.py` — PySide6 main window.
- `gt_editor/graphics_scene.py` — table/cell/grid scene rendering.
- `gt_editor/models.py` — in-memory table model.
- `gt_editor/commands.py` — undoable grid/cell operations.
- `gt_editor/io_docling.py` — load/export Docling-style records.
- `gt_editor/text_assign.py` — text assignment after grid edits.
- `gt_editor/validation.py` — export validation helpers.
- `gt_editor/docling_validator.py` — bundled Docling-style schema/PNG validator used by tests and exports.

## Current status

MVP editor: load paired PNG/JSON, inspect table grid/cells, apply command-stack grid/cell operations, assign/export text, and validate exported JSON. The bundled tests and smoke commands are the portability gate.
