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

Launch the GUI with bundled samples:

```bash
gt-editor \
  --image-dir gt_editor_samples/image \
  --json-dir gt_editor_samples/json \
  --export-dir gt_editor_exports
```

Headless smoke check:

```bash
gt-editor --headless-smoke --limit 3
python scripts/smoke_gt_editor.py --out-dir /tmp/gt_editor_smoke
```

GUI smoke check for headless/CI environments:

```bash
QT_QPA_PLATFORM=offscreen gt-editor --smoke-exit --limit 3
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
