#!/usr/bin/env python3
"""tte_validate.py — integrity (정합성) checker for docling-compatible table records.

`validate(record, png_path)` -> (ok: bool, errors: list[str]). Used inline by
tte_extract.py (a failing table is not written) and as a standalone audit CLI over
an existing output tree (-> _integrity_report.md + _integrity_fail.tsv).

The checker is calibrated against the GROUND-TRUTH val schema
(Datasets/table_structure_val/json/*.json). Two conventions locked by measuring
real val files (must hold or known-good val data would fail):

  - INTERIOR gridlines: len(h_lines) == num_rows - 1 and len(v_lines) == num_cols - 1
    (a 26-row table has 25 h_lines; a 2-col table has 1 v_line). Degenerate small
    grids (num<=1) carry 0 lines.
  - table_bbox_px width/height ~= image_size (val ratio measured 0.999..1.001), but
    bbox origin is the PAGE, not 0 (val l~83, t~322). So we check SIZE not origin.

Tolerances: TOL_PX=2 for cell/line bounds and bbox-vs-image size (rounding); cells
may exceed the image by up to MAX_OVERSHOOT_FRAC of the dimension before it FAILs.
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List, Tuple

EXPECTED_TOP_KEYS = [
    "source_pdf", "page_no", "table_index", "global_index", "num_rows",
    "num_cols", "image_size", "table_bbox_px", "h_lines", "v_lines", "cells",
    "layout_tedss_score",
]
EXPECTED_CELL_KEYS = [
    "row", "col", "end_row", "end_col", "row_span", "col_span", "text",
    "is_column_header", "is_row_header", "is_row_section", "is_fillable", "bbox_px",
]

TOL_PX = 2.0
# Cell geometry overshoot allowance: max(6px, 1.5% of the dimension). Loosened
# from 1% (D3): real val files have cells a few px outside SMALL images (e.g. a
# cell 4.7px past a 121px-high image = 3.9%); val is ground truth so the bound
# must accept it. The 6px absolute floor covers these tiny-image cases; for our
# own DPI=300 outputs (thousands of px) the 1.5% fractional term dominates.
MAX_OVERSHOOT_FRAC = 0.015
MIN_OVERSHOOT_PX = 6.0
# Fill-ratio tripwire (D4): WARN (not fail) when a multi-col grid's covered
# fraction drops below this — a regression signal for D1-class column collapse.
FILL_WARN_THRESHOLD = 0.3


def compute_warnings(record: Dict[str, Any]) -> List[str]:
    """Non-fatal integrity tripwires (D4). These do NOT fail a record; they are a
    regression signal logged to _integrity_report.md. Detects D1-class column
    loss that the hard `coverage==0` gate cannot see:

      - low fill ratio (< FILL_WARN_THRESHOLD) on a grid with > 1 column, and
      - an entire interior column with zero cells while the table had many cells.

    Some tables are legitimately sparse (val min fill ~0.26), so this is a WARN,
    not a FAIL.
    """
    warns: List[str] = []
    nr = record.get("num_rows")
    nc = record.get("num_cols")
    cells = record.get("cells")
    if not (_is_int(nr) and _is_int(nc) and isinstance(cells, list)):
        return warns
    if nr <= 0 or nc <= 0:
        return warns

    covered = set()
    col_hit = [0] * nc
    for c in cells:
        if not isinstance(c, dict):
            continue
        row, col = c.get("row"), c.get("col")
        er, ec = c.get("end_row"), c.get("end_col")
        if not all(_is_int(x) for x in (row, col, er, ec)):
            continue
        for rr in range(row, er):
            for cc in range(col, ec):
                if 0 <= cc < nc:
                    covered.add((rr, cc))
        for cc in range(col, ec):
            if 0 <= cc < nc:
                col_hit[cc] += 1

    fill = len(covered) / float(nr * nc)
    if nc > 1 and fill < FILL_WARN_THRESHOLD:
        warns.append(f"W: low fill ratio {fill:.3f} on {nr}x{nc} grid "
                     f"({len(cells)} cells) — possible column collapse")
    if nc > 1 and len(cells) >= nc and any(h == 0 for h in col_hit):
        empties = [i for i, h in enumerate(col_hit) if h == 0]
        warns.append(f"W: interior columns with zero cells {empties} on "
                     f"{nr}x{nc} grid with {len(cells)} cells")
    return warns


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v):
    return _is_num(v) and math.isfinite(v)


def validate(record: Dict[str, Any], png_path: str = None,
             check_png: bool = True, strict: bool = False,
             drops: Dict[str, int] = None) -> Tuple[bool, List[str]]:
    """Run all integrity checks on one record (+ optional png). Returns (ok, errors).

    `check_png=False` skips check D (used when calibrating against records whose
    PNG is in a sibling image dir, or for pure-record tests).

    Two calibration regimes (F2/F5/F6):
      - DEFAULT (strict=False): the lenient "val-calibration" mode used to audit the
        ground-truth `table_structure_val` schema, which legitimately carries
        anomalies (float layout_tedss_score, non-monotonic / non-contiguous line
        keys). Keeps val passing ~1759/1761.
      - strict=True: "output-strict" mode for OUR OWN emitted TTE records. It is a
        REAL integrity gate (hard-fails) — in addition to all default checks it
        requires:
          * E (F2): no distinct-text leaf lost during mapping. Pass `drops` (from
            tte_map meta); a positive `drops["grid_collision"]` is real data loss
            and FAILS. Genuine empty-source columns / low fill stay WARN-only.
          * line keys (F5): h_lines/v_lines keys are NONNEGATIVE int strings in
            ascending order (our emitter guarantees this).
          * layout_tedss_score (F6): must be null (None).
        These are NOT applied to val (val violates them), only to emitted output.
    """
    errors: List[str] = []

    # ---- A. schema / types (incl. EXACT key order) ----
    keys = list(record.keys())
    if keys != EXPECTED_TOP_KEYS:
        errors.append(f"A: top-level key order/set mismatch: {keys}")
        # cannot reliably continue if keys are wrong; but try the rest defensively
    sp = record.get("source_pdf")
    if not (isinstance(sp, str) and sp.endswith(".pdf")):
        errors.append(f"A: source_pdf not a .pdf string: {sp!r}")
    for k in ("page_no",):
        if not (_is_int(record.get(k)) and record[k] >= 1):
            errors.append(f"A: {k} must be int>=1: {record.get(k)!r}")
    for k in ("table_index", "global_index", "num_rows", "num_cols"):
        if not (_is_int(record.get(k)) and record[k] >= 0):
            errors.append(f"A: {k} must be int>=0: {record.get(k)!r}")

    img = record.get("image_size")
    img_ok = (isinstance(img, list) and len(img) == 2
              and _is_int(img[0]) and _is_int(img[1]) and img[0] > 0 and img[1] > 0)
    if not img_ok:
        errors.append(f"A: image_size must be [int>0,int>0]: {img!r}")
    iw, ih = (img[0], img[1]) if img_ok else (None, None)

    bbox = record.get("table_bbox_px")
    bbox_ok = (isinstance(bbox, list) and len(bbox) == 4 and all(_finite(x) for x in bbox))
    if not bbox_ok:
        errors.append(f"A: table_bbox_px must be 4 finite floats: {bbox!r}")
    elif not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
        errors.append(f"A: table_bbox_px not l<r,t<b: {bbox!r}")

    for name in ("h_lines", "v_lines"):
        d = record.get(name)
        if not isinstance(d, dict):
            errors.append(f"A: {name} must be a dict: {type(d).__name__}")
            continue
        ints = []
        key_order = []  # insertion order of parsed int keys (for F5)
        for k, v in d.items():
            if not (isinstance(k, str) and k.lstrip("-").isdigit()):
                errors.append(f"A: {name} key not str(int): {k!r}")
                continue
            ints.append(int(k))
            key_order.append(int(k))
            if not _finite(v):
                errors.append(f"A: {name}[{k}] not finite float: {v!r}")
        # D3: val LEGITIMATELY uses non-contiguous integer keys (gaps, or a base
        # other than 0 — verified across the real val set). Accept ANY sorted set
        # of unique str-ints with finite values; do NOT require 0..n-1 contiguity.
        # (Our own emitter still produces contiguous keys; this only loosens the
        # acceptance bound so ground-truth val data passes.)
        if len(ints) != len(set(ints)):
            errors.append(f"A: {name} has duplicate keys: {sorted(ints)}")
        # F5 (output-strict only): require nonnegative int keys in ascending
        # insertion order. Our emitter guarantees this; val has negative/unsorted
        # anomalies so this is NOT applied in calibration mode.
        if strict:
            if any(x < 0 for x in key_order):
                errors.append(f"A(strict): {name} has negative key(s): {key_order}")
            if key_order != sorted(key_order):
                errors.append(f"A(strict): {name} keys not ascending: {key_order}")

    tedss = record.get("layout_tedss_score")
    if strict:
        # F6 (output-strict): emitted records MUST carry null. val has floats, so
        # this is enforced ONLY for our own output.
        if tedss is not None:
            errors.append(f"A(strict): layout_tedss_score must be null: {tedss!r}")
    elif not (tedss is None or _finite(tedss)):
        errors.append(f"A: layout_tedss_score must be null or finite float: {tedss!r}")

    cells = record.get("cells")
    if not isinstance(cells, list):
        errors.append(f"A: cells must be a list: {type(cells).__name__}")
        cells = []
    for ci, c in enumerate(cells):
        if not isinstance(c, dict):
            errors.append(f"A: cell[{ci}] not a dict")
            continue
        if list(c.keys()) != EXPECTED_CELL_KEYS:
            errors.append(f"A: cell[{ci}] key order/set mismatch: {list(c.keys())}")
        for k in ("row", "col", "end_row", "end_col", "row_span", "col_span"):
            if not _is_int(c.get(k)):
                errors.append(f"A: cell[{ci}].{k} not int: {c.get(k)!r}")
        if not isinstance(c.get("text"), str):
            errors.append(f"A: cell[{ci}].text not str")
        for k in ("is_column_header", "is_row_header", "is_row_section", "is_fillable"):
            if not isinstance(c.get(k), bool):
                errors.append(f"A: cell[{ci}].{k} not bool: {c.get(k)!r}")
        bb = c.get("bbox_px")
        if not (isinstance(bb, list) and len(bb) == 4 and all(_finite(x) for x in bb)):
            errors.append(f"A: cell[{ci}].bbox_px not 4 finite floats")

    num_rows = record.get("num_rows") if _is_int(record.get("num_rows")) else 0
    num_cols = record.get("num_cols") if _is_int(record.get("num_cols")) else 0

    # ---- B. grid consistency (INTERIOR-line convention) ----
    hl = record.get("h_lines") if isinstance(record.get("h_lines"), dict) else {}
    vl = record.get("v_lines") if isinstance(record.get("v_lines"), dict) else {}
    # INTERIOR-line convention: a grid has AT MOST num-1 interior lines, but may
    # have fewer where dividers are absent (verified in val: 013d57.. is 4x14 with
    # only 11 v_lines). So the bound is <=, not ==. (Our own mapper emits exactly
    # num-1; this looser bound keeps the checker valid against val ground truth.)
    max_h = max(0, num_rows - 1)
    max_v = max(0, num_cols - 1)
    if len(hl) > max_h:
        errors.append(f"B: len(h_lines)={len(hl)} > num_rows-1={max_h}")
    if len(vl) > max_v:
        errors.append(f"B: len(v_lines)={len(vl)} > num_cols-1={max_v}")

    key_texts: Dict[tuple, list] = {}
    covered = set()
    for ci, c in enumerate(cells):
        if not isinstance(c, dict):
            continue
        row, col = c.get("row"), c.get("col")
        er, ec = c.get("end_row"), c.get("end_col")
        rs, cs = c.get("row_span"), c.get("col_span")
        if not all(_is_int(x) for x in (row, col, er, ec, rs, cs)):
            continue
        if not (0 <= row < er <= max(num_rows, er)):
            errors.append(f"B: cell[{ci}] row range bad: {row}..{er} (num_rows={num_rows})")
        if not (0 <= col < ec <= max(num_cols, ec)):
            errors.append(f"B: cell[{ci}] col range bad: {col}..{ec} (num_cols={num_cols})")
        if er > num_rows:
            errors.append(f"B: cell[{ci}] end_row {er} > num_rows {num_rows}")
        if ec > num_cols:
            errors.append(f"B: cell[{ci}] end_col {ec} > num_cols {num_cols}")
        if rs != er - row:
            errors.append(f"B: cell[{ci}] row_span {rs} != end_row-row {er-row}")
        if cs != ec - col:
            errors.append(f"B: cell[{ci}] col_span {cs} != end_col-col {ec-col}")
        key = (row, col, er, ec)
        key_texts.setdefault(key, []).append(c.get("text"))
        for rr in range(row, er):
            for cc in range(col, ec):
                covered.add((rr, cc))

    # Duplicate-key handling (D1): a SAME-TEXT duplicate key is a real dedup
    # failure (FAIL). A DISTINCT-TEXT duplicate key is a genuine grid_collision
    # (two different cells the coarse binary grid cannot separate); the data is
    # preserved, val ground truth never hits this, and D1 specifies "keep all" —
    # so it is NOT a hard fail here. (compute_warnings does not re-flag it; it is
    # already logged in the mapper's collapse_log.)
    for key, txts in key_texts.items():
        if len(txts) > 1 and len(set(txts)) <= 1:
            errors.append(f"B: duplicate grid key {key} same text (dedup failed)")

    coverage = None
    if num_rows > 0 and num_cols > 0:
        coverage = len(covered) / float(num_rows * num_cols)
        if coverage == 0 and cells:
            errors.append("B: coverage==0 on a non-empty grid")

    # ---- C. geometry within image ----
    if img_ok:
        ox = max(MIN_OVERSHOOT_PX, MAX_OVERSHOOT_FRAC * iw)
        oy = max(MIN_OVERSHOOT_PX, MAX_OVERSHOOT_FRAC * ih)
        for ci, c in enumerate(cells):
            bb = c.get("bbox_px") if isinstance(c, dict) else None
            if not (isinstance(bb, list) and len(bb) == 4 and all(_finite(x) for x in bb)):
                continue
            cl, ct, cr, cb = bb
            if cl > cr or ct > cb:
                errors.append(f"C: cell[{ci}] bbox not l<=r,t<=b: {bb}")
            if cl < -(TOL_PX + ox) or cr > iw + TOL_PX + ox:
                errors.append(f"C: cell[{ci}] x out of image [0,{iw}]: {cl:.1f},{cr:.1f}")
            if ct < -(TOL_PX + oy) or cb > ih + TOL_PX + oy:
                errors.append(f"C: cell[{ci}] y out of image [0,{ih}]: {ct:.1f},{cb:.1f}")
        # Gridlines use the SAME overshoot tolerance as cells (D3): val gridlines
        # can sit a few px outside small crops just like cells do.
        for k, v in hl.items():
            if _finite(v) and not (-(TOL_PX + oy) <= v <= ih + TOL_PX + oy):
                errors.append(f"C: h_lines[{k}]={v:.1f} out of [0,{ih}]")
        for k, v in vl.items():
            if _finite(v) and not (-(TOL_PX + ox) <= v <= iw + TOL_PX + ox):
                errors.append(f"C: v_lines[{k}]={v:.1f} out of [0,{iw}]")
        if bbox_ok:
            bw = bbox[2] - bbox[0]
            bh = bbox[3] - bbox[1]
            if abs(bw - iw) > TOL_PX:
                errors.append(f"C: table_bbox width {bw:.1f} != image_w {iw} (+/-{TOL_PX})")
            if abs(bh - ih) > TOL_PX:
                errors.append(f"C: table_bbox height {bh:.1f} != image_h {ih} (+/-{TOL_PX})")

    # ---- D. file on disk ----
    if check_png:
        if not png_path or not os.path.isfile(png_path):
            errors.append(f"D: png missing: {png_path}")
        elif os.path.getsize(png_path) == 0:
            errors.append(f"D: png is 0-byte: {png_path}")
        else:
            try:
                from PIL import Image
                with Image.open(png_path) as im:
                    if img_ok and list(im.size) != [iw, ih]:
                        errors.append(f"D: png size {im.size} != image_size {[iw, ih]}")
            except Exception as e:
                errors.append(f"D: png open failed: {type(e).__name__}: {e}")

    # ---- E. output-strict data-loss gate (F2) ----
    # Real-data-loss signal: a DISTINCT-text leaf dropped during mapping. Genuine
    # empty-source columns / low fill are NOT failed here (they stay WARN in
    # compute_warnings) — only measured grid_collision loss fails. Requires `drops`
    # (from tte_map meta); absent that we cannot prove loss, so we do not guess.
    if strict and drops is not None:
        gc = drops.get("grid_collision", 0)
        if gc and gc > 0:
            errors.append(
                f"E(strict): {gc} distinct-text leaf/leaves dropped during mapping "
                f"(grid_collision data loss — D1-style column collapse)")

    return (len(errors) == 0), errors


# ---------------------------------------------------------------------------
# Standalone audit CLI
# ---------------------------------------------------------------------------

def _load_drops_tsv(output_root: str) -> Dict[tuple, Dict[str, int]]:
    """Load _run/_drops.tsv (written by tte_extract) into {(sub, stem_gi): drops}.
    Keyed by the output stem (`<stem>_<NNNN>`) so it lines up with each json file.
    Returns {} if the file is absent."""
    path = os.path.join(output_root, "_run", "_drops.tsv")
    out: Dict[tuple, Dict[str, int]] = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            header = f.readline()
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 8:
                    continue
                sub, stem, gi, lin, cout, td, ba, gc = parts[:8]
                key = (sub, f"{stem}_{int(gi):04d}")
                out[key] = {"true_dup": int(td), "band_artifact": int(ba),
                            "grid_collision": int(gc)}
    except Exception:
        return out
    return out


def _audit(output_root: str, strict: bool = False) -> int:
    """Re-audit an existing output tree: <root>/<sub>/json/*.json with sibling
    <root>/<sub>/image/<stem>.png. Writes _run/_integrity_report.md + _fail.tsv.

    `strict=True` (F2/F5/F6 output-strict mode) turns the emitted-output integrity
    contract into a HARD gate: distinct-text leaf loss (from _run/_drops.tsv), bad
    line-key shapes, and non-null layout_tedss_score FAIL. Exit is nonzero on any
    failure. Default (lenient) mode is for val calibration.
    """
    import glob
    from collections import Counter

    run_dir = os.path.join(output_root, "_run")
    os.makedirs(run_dir, exist_ok=True)
    json_files = sorted(glob.glob(os.path.join(output_root, "*", "json", "*.json")))
    drops_map = _load_drops_tsv(output_root) if strict else {}

    total = 0
    passed = 0
    cat_counter = Counter()
    fail_rows = []
    warn_rows = []
    for jf in json_files:
        total += 1
        try:
            rec = json.loads(open(jf, encoding="utf-8").read())
        except Exception as e:
            cat_counter["file"] += 1
            fail_rows.append(("?", os.path.basename(jf), "?", "file", f"json parse: {e}"))
            continue
        sub = os.path.basename(os.path.dirname(os.path.dirname(jf)))
        stem = os.path.splitext(os.path.basename(jf))[0]
        png = os.path.join(output_root, sub, "image", stem + ".png")
        drops = drops_map.get((sub, stem)) if strict else None
        ok, errs = validate(rec, png, strict=strict, drops=drops)
        warns = compute_warnings(rec)
        if warns:
            warn_rows.append((sub, stem, str(rec.get("global_index", "?")),
                              " | ".join(warns)))
        if ok:
            passed += 1
        else:
            cats = set(e.split(":", 1)[0] for e in errs)
            for cat in cats:
                cat_counter[{"A": "schema", "B": "grid", "C": "geometry", "D": "file"}.get(cat, cat)] += 1
            fail_rows.append((sub, stem, str(rec.get("global_index", "?")),
                              ",".join(sorted(cats)), " | ".join(errs[:5])))

    report = os.path.join(run_dir, "_integrity_report.md")
    with open(report, "w", encoding="utf-8") as f:
        f.write("# Integrity report\n\n")
        f.write(f"- mode: {'OUTPUT-STRICT (F2/F5/F6 hard gate)' if strict else 'lenient (val-calibration)'}\n")
        f.write(f"- json files audited: {total}\n")
        f.write(f"- PASS: {passed}\n")
        f.write(f"- FAIL: {total - passed}\n")
        f.write(f"- WARN (fill/empty-column tripwire, non-fatal): {len(warn_rows)}\n\n")
        f.write("## fail categories\n\n")
        for cat, n in cat_counter.most_common():
            f.write(f"- {cat}: {n}\n")
        f.write("\n## top offenders\n\n")
        for row in fail_rows[:30]:
            f.write(f"- {row[0]}/{row[1]} #{row[2]} [{row[3]}] {row[4]}\n")
        f.write("\n## warnings (regression tripwire — NOT failures)\n\n")
        for row in warn_rows[:30]:
            f.write(f"- {row[0]}/{row[1]} #{row[2]} {row[3]}\n")
        if len(warn_rows) > 30:
            f.write(f"- ... and {len(warn_rows) - 30} more\n")

    tsv = os.path.join(run_dir, "_integrity_fail.tsv")
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("sub\tstem\tglobal_index\tcategory\tmessage\n")
        for row in fail_rows:
            f.write("\t".join(row) + "\n")

    print(f"audited {total}, pass {passed}, fail {total - passed}, "
          f"warn {len(warn_rows)}; report -> {report}")
    return 0 if (total - passed) == 0 else 1


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Audit a docling table output tree.")
    ap.add_argument("output_root", help="root containing <sub>/json + <sub>/image")
    ap.add_argument("--strict", action="store_true",
                    help="output-strict mode (F2/F5/F6 hard gate; nonzero exit on fail)")
    args = ap.parse_args()
    sys.exit(_audit(args.output_root, strict=args.strict))
