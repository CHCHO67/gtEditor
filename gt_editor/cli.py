"""Command-line entry point for the GT editor."""
from __future__ import annotations

import argparse
import os
import sys
from .io_docling import discover_pairs, load_document
from .text_assign import assign_text_to_document


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PySide table GT editor MVP")
    p.add_argument("--image-dir", default="gt_editor_samples/image", help="Directory containing cropped table PNGs")
    p.add_argument("--json-dir", default="gt_editor_samples/json", help="Directory containing matching Docling-compatible JSON files")
    p.add_argument("--export-dir", default="gt_editor_exports", help="Directory for saved JSON/project files")
    p.add_argument("--smoke-exit", action="store_true", help="Load the first pair through the GUI app path and exit without showing the window")
    p.add_argument("--headless-smoke", action="store_true", help="Load/assign all pairs without importing PySide")
    p.add_argument("--list-pairs", action="store_true", help="List discovered pairs and exit")
    p.add_argument("--limit", type=int, default=None, help="Limit pairs for list/smoke")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pairs = discover_pairs(args.image_dir, args.json_dir)
    if args.limit is not None and args.limit > 0:
        pairs = pairs[:args.limit]
    if args.list_pairs:
        for image_path, json_path in pairs:
            print(f"{image_path.stem}\t{image_path}\t{json_path}")
        return 0
    if not pairs:
        print(f"No PNG/JSON pairs found in {args.image_dir} and {args.json_dir}", file=sys.stderr)
        return 1
    if args.headless_smoke:
        total_warnings = 0
        for image_path, json_path in pairs:
            doc = assign_text_to_document(load_document(image_path, json_path))
            total_warnings += len(doc.warnings)
        print(f"headless-smoke ok pairs={len(pairs)} warnings={total_warnings}")
        return 0

    if args.smoke_exit:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from .app import build_app
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    app, win = build_app(args.image_dir, args.json_dir, args.export_dir)
    if args.smoke_exit:
        loaded = win.doc is not None
        print(f"gui-smoke ok pairs={len(pairs)} loaded={loaded} warnings={len(win.doc.warnings) if win.doc else 0}")
        return 0 if loaded else 1
    win.show()
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
