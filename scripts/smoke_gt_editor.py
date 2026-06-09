#!/usr/bin/env python3
"""Load/export/validate smoke for gt_editor over a directory of sample pairs."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_editor.io_docling import discover_pairs, export_docling, load_document, write_json  # noqa: E402
from gt_editor.text_assign import assign_text_to_document  # noqa: E402
from gt_editor.validation import validate_record  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-dir", default="gt_editor_samples/image")
    ap.add_argument("--json-dir", default="gt_editor_samples/json")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--summary", default=None)
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pairs = discover_pairs(args.image_dir, args.json_dir)
    rows = []
    failures = []
    for image_path, json_path in pairs:
        doc = assign_text_to_document(load_document(image_path, json_path))
        record = export_docling(doc)
        ok, errors = validate_record(record, image_path, check_png=True)
        out_path = out_dir / json_path.name
        write_json(out_path, record)
        rows.append({
            "stem": json_path.stem,
            "ok": ok,
            "warnings": len(doc.warnings),
            "rows": doc.num_rows,
            "cols": doc.num_cols,
            "cells": len(doc.cells),
            "out": str(out_path),
            "errors": " | ".join(errors[:5]),
        })
        if not ok:
            failures.append((json_path.stem, errors))
    summary_path = Path(args.summary) if args.summary else out_dir / "summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["stem", "ok", "warnings", "rows", "cols", "cells", "out", "errors"])
        writer.writeheader(); writer.writerows(rows)
    print(f"gt-editor-smoke pairs={len(pairs)} ok={len(pairs)-len(failures)} failed={len(failures)} summary={summary_path}")
    total_warnings = sum(int(r["warnings"]) for r in rows)
    print(f"warnings={total_warnings}")
    if failures:
        for stem, errors in failures[:10]:
            print(f"FAIL {stem}: {errors[:3]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
