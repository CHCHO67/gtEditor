"""Command-line entry point for the GT editor."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from io_docling import (
    discover_input_datasets,
    legacy_input_dataset,
    load_document,
    save_output_tab,
    verify_output_tab_counts,
)
from text_assign import assign_text_to_document


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PySide table GT editor")
    p.add_argument(
        "--input-data",
        action="append",
        default=[],
        help="Input_data folder containing image/ and json/ children; repeat for multiple GUI tabs",
    )
    p.add_argument("--output-data", default=None, help="Output_data folder for saved image/json tabs")
    p.add_argument("--image-dir", default="gt_editor_samples/image", help="Legacy directory containing cropped table PNGs")
    p.add_argument("--json-dir", default="gt_editor_samples/json", help="Legacy directory containing matching Docling-compatible JSON files")
    p.add_argument("--export-dir", default="gt_editor_exports", help="Legacy directory for saved JSON/project files")
    p.add_argument("--smoke-exit", action="store_true", help="Load the first pair through the GUI app path and exit without showing the window")
    p.add_argument("--headless-smoke", action="store_true", help="Load/assign all pairs without importing PySide")
    p.add_argument("--save-all", action="store_true", help="Headlessly write every input pair to --output-data and exit")
    p.add_argument("--list-pairs", action="store_true", help="List discovered pairs and exit")
    p.add_argument("--limit", type=int, default=None, help="Limit pairs per tab for list/smoke")
    return p.parse_args(argv)


def _datasets(args: argparse.Namespace):
    datasets = discover_input_datasets(args.input_data) if args.input_data else [legacy_input_dataset(args.image_dir, args.json_dir)]
    if args.limit is not None and args.limit > 0:
        from io_docling import InputDataset

        datasets = [
            InputDataset(
                name=d.name,
                root=d.root,
                image_dir=d.image_dir,
                json_dir=d.json_dir,
                pairs=tuple(d.pairs[: args.limit]),
            )
            for d in datasets
        ]
    return datasets


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        datasets = _datasets(args)
    except (FileNotFoundError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    total_pairs = sum(len(dataset.pairs) for dataset in datasets)
    if args.list_pairs:
        for dataset in datasets:
            for pair in dataset.pairs:
                print(f"{dataset.name}\t{pair.stem}\t{pair.image_path}\t{pair.json_path}")
        return 0
    if total_pairs == 0:
        roots = ", ".join(str(dataset.root) for dataset in datasets)
        print(f"No PNG/JSON pairs found in {roots}", file=sys.stderr)
        return 1
    if args.headless_smoke:
        total_warnings = 0
        for dataset in datasets:
            for pair in dataset.pairs:
                doc = assign_text_to_document(load_document(pair.image_path, pair.json_path))
                total_warnings += len(doc.warnings)
        print(f"headless-smoke ok tabs={len(datasets)} pairs={total_pairs} warnings={total_warnings}")
        return 0
    if args.save_all:
        output_data = args.output_data
        if not output_data:
            print("--save-all requires --output-data", file=sys.stderr)
            return 2
        total_saved = 0
        for dataset in datasets:
            results = save_output_tab(dataset, output_data)
            total_saved += len(results)
            ok, counts = verify_output_tab_counts(output_data, dataset)
            print(counts)
            if not ok:
                return 1
        print(f"save-all ok tabs={len(datasets)} pairs={total_saved} output={Path(output_data)}")
        return 0

    if args.smoke_exit:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from app import build_app
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    app, win = build_app(
        args.image_dir,
        args.json_dir,
        args.export_dir,
        input_data=args.input_data or None,
        output_data=args.output_data or args.export_dir,
    )
    if args.smoke_exit:
        loaded = win.doc is not None
        print(
            f"gui-smoke ok tabs={len(win.sessions)} pairs={total_pairs} loaded={loaded} "
            f"warnings={len(win.doc.warnings) if win.doc else 0}"
        )
        return 0 if loaded else 1
    win.show()
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
