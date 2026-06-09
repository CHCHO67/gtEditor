from gt_editor.io_docling import discover_pairs, load_document
from gt_editor.text_assign import assign_text_to_document


def test_crossing_warnings_include_both_axes_for_prioritized_sample():
    pairs = discover_pairs("gt_editor_samples/image", "gt_editor_samples/json")
    target = next((p for p in pairs if p.stem.startswith("b90923")), pairs[0])
    doc = assign_text_to_document(load_document(target.image_path, target.json_path))
    candidates = {w.candidate for w in doc.warnings}
    assert "left_right_split" in candidates
    assert "top_bottom_split" in candidates
    assert all(w.bbox.left <= w.line_coordinate <= w.bbox.right if w.axis == "vertical" else w.bbox.top <= w.line_coordinate <= w.bbox.bottom for w in doc.warnings[:20])


def test_every_nonempty_span_gets_assignment_when_cells_exist():
    pair = discover_pairs("gt_editor_samples/image", "gt_editor_samples/json")[0]
    doc = assign_text_to_document(load_document(pair.image_path, pair.json_path))
    nonempty = [s for s in doc.text_spans if s.text.strip()]
    assert nonempty
    assert all(s.assigned_cell_key is not None for s in nonempty)
    assigned_ids = {sid for cell in doc.cells for sid in cell.assigned_span_ids}
    assert {s.span_id for s in nonempty}.issubset(assigned_ids)
