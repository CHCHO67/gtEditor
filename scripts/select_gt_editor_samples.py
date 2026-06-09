#!/usr/bin/env python3
"""Select 10 table_structure_val samples for GT editor smoke testing.

Prioritize cases where existing text bboxes cross interior grid lines:
- vertical line crossing text bbox => horizontally connected text box, needs left/right split
- horizontal line crossing text bbox => vertically connected text box, needs top/bottom split
"""
from __future__ import annotations

import csv, json, random, shutil
from pathlib import Path

ROOTS = [
    Path('/home/chanho_cho/Datasets/table_structure_val'),
    Path('/home/chanho-cho/Datasets/table_structure_val'),
    Path('/home/chanho-cho/projects/Datasets/table_structure_val'),
]
SRC = next((p for p in ROOTS if (p/'image').is_dir() and (p/'json').is_dir()), None)
if SRC is None:
    raise SystemExit('No table_structure_val root found')
OUT = Path('/home/chanho-cho/projects/taggedPDF/Robin_TTE/gt_editor_samples')
SEED = 20260608
N = 10
TOL = 2.0

def floats_from_dict(d):
    return sorted(float(v) for v in (d or {}).values())

def crossing_score(rec):
    h = floats_from_dict(rec.get('h_lines'))
    v = floats_from_dict(rec.get('v_lines'))
    img = rec.get('image_size') or [0, 0]
    W, H = float(img[0]), float(img[1])
    horiz = 0  # vertical grid line crosses text bbox: needs left/right split
    vert = 0   # horizontal grid line crosses text bbox: needs top/bottom split
    both_cells = 0
    wide = 0
    tall = 0
    examples = []
    for ci, c in enumerate(rec.get('cells') or []):
        bb = c.get('bbox_px')
        if not (isinstance(bb, list) and len(bb) == 4):
            continue
        x0, y0, x1, y1 = map(float, bb)
        cx = [x for x in v if x0 + TOL < x < x1 - TOL]
        cy = [y for y in h if y0 + TOL < y < y1 - TOL]
        if cx:
            horiz += len(cx)
        if cy:
            vert += len(cy)
        if cx and cy:
            both_cells += 1
        if x1 - x0 > 0.45 * W:
            wide += 1
        if y1 - y0 > 0.18 * H:
            tall += 1
        if (cx or cy) and len(examples) < 4:
            text = (c.get('text') or '').replace('\n', ' ')[:80]
            examples.append(f"cell#{ci} r{c.get('row')}c{c.get('col')} bbox={[round(z,1) for z in bb]} crosses_v={len(cx)} crosses_h={len(cy)} text={text!r}")
    score = horiz * 3 + vert * 3 + both_cells * 5 + wide + tall
    return score, horiz, vert, both_cells, wide, tall, examples

candidates = []
for jf in sorted((SRC/'json').glob('*.json')):
    stem = jf.stem
    img = SRC/'image'/(stem + '.png')
    if not img.is_file():
        continue
    try:
        rec = json.loads(jf.read_text(encoding='utf-8'))
    except Exception:
        continue
    score, horiz, vert, both, wide, tall, examples = crossing_score(rec)
    if score <= 0:
        continue
    candidates.append({
        'stem': stem,
        'score': score,
        'horizontal_split_signals': horiz,
        'vertical_split_signals': vert,
        'both_axis_cells': both,
        'wide_text_boxes': wide,
        'tall_text_boxes': tall,
        'num_rows': rec.get('num_rows'),
        'num_cols': rec.get('num_cols'),
        'cells': len(rec.get('cells') or []),
        'image_path': str(img),
        'json_path': str(jf),
        'examples': ' || '.join(examples),
    })
# Top band + random for diversity. Ensure both horizontal and vertical cases.
candidates.sort(key=lambda r: (-r['score'], r['stem']))
rng = random.Random(SEED)
top = candidates[:max(50, N*5)]
# stratify: pick 4 with horiz, 4 with vert, 2 mixed/random.
selected = []
def add_pool(pool, k):
    pool = [r for r in pool if r['stem'] not in {x['stem'] for x in selected}]
    rng.shuffle(pool)
    selected.extend(pool[:k])
add_pool([r for r in top if r['horizontal_split_signals'] > 0], 4)
add_pool([r for r in top if r['vertical_split_signals'] > 0], 4)
add_pool(top, N-len(selected))
selected = selected[:N]
OUT.mkdir(parents=True, exist_ok=True)
(OUT/'image').mkdir(exist_ok=True)
(OUT/'json').mkdir(exist_ok=True)
for d in (OUT/'image', OUT/'json'):
    for p in d.glob('*'):
        if p.is_file() or p.is_symlink(): p.unlink()
for r in selected:
    # symlink to keep samples lightweight
    img_dst = OUT/'image'/(r['stem']+'.png')
    json_dst = OUT/'json'/(r['stem']+'.json')
    img_dst.symlink_to(r['image_path'])
    json_dst.symlink_to(r['json_path'])
fields = ['stem','score','horizontal_split_signals','vertical_split_signals','both_axis_cells','wide_text_boxes','tall_text_boxes','num_rows','num_cols','cells','image_path','json_path','examples']
with (OUT/'sample_manifest.csv').open('w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader(); w.writerows(selected)
with (OUT/'candidate_top50.csv').open('w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader(); w.writerows(top[:50])
print(json.dumps({
    'src': str(SRC),
    'out': str(OUT),
    'candidate_count': len(candidates),
    'selected_count': len(selected),
    'seed': SEED,
    'selected': [{k:r[k] for k in ('stem','score','horizontal_split_signals','vertical_split_signals','num_rows','num_cols')} for r in selected],
}, ensure_ascii=False, indent=2))
