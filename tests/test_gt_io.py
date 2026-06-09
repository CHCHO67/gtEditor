from pathlib import Path

from gt_editor.io_docling import discover_pairs, export_docling, load_document, validate_docling_record
from gt_editor.text_assign import assign_text_to_document

SAMPLE_IMAGE = Path("gt_editor_samples/image")
SAMPLE_JSON = Path("gt_editor_samples/json")


def test_discover_pairs_has_selected_ten():
    pairs = discover_pairs(SAMPLE_IMAGE, SAMPLE_JSON)
    assert len(pairs) == 10
    assert all(pair.image_path.stem == pair.json_path.stem == pair.stem for pair in pairs)
    image_path, json_path = pairs[0]
    assert image_path == pairs[0].image_path
    assert json_path == pairs[0].json_path


def test_load_assign_export_validate_first_sample():
    pair = discover_pairs(SAMPLE_IMAGE, SAMPLE_JSON)[0]
    doc = assign_text_to_document(load_document(pair.image_path, pair.json_path))
    assert doc.num_rows >= 1
    assert doc.num_cols >= 1
    assert len(doc.x_edges) == doc.num_cols + 1
    assert len(doc.y_edges) == doc.num_rows + 1
    assert doc.warnings
    record = export_docling(doc)
    assert list(record.keys()) == [
        "source_pdf", "page_no", "table_index", "global_index", "num_rows", "num_cols",
        "image_size", "table_bbox_px", "h_lines", "v_lines", "cells", "layout_tedss_score",
    ]
    ok, errors = validate_docling_record(record, pair.image_path, check_png=True)
    assert ok, errors
