from dataclasses import replace
import json

from io_docling import discover_pairs, export_docling, load_document, save_docling_json, validate_docling_record
from text_assign import assign_text_to_document


SAMPLE_IMAGE = "Input_data/tagged_table_PDF_1k_TTEcrop_passed/normal/image"
SAMPLE_JSON = "Input_data/tagged_table_PDF_1k_TTEcrop_passed/normal/json_formatted"
FINANCIAL_IMAGE = "Input_data/tagged_table_PDF_1k_TTEcrop_passed/financial_style/image"
FINANCIAL_JSON = "Input_data/tagged_table_PDF_1k_TTEcrop_passed/financial_style/json_formatted"


def test_all_selected_samples_export_validate():
    pairs = discover_pairs(SAMPLE_IMAGE, SAMPLE_JSON)
    assert len(pairs) >= 10
    warning_counts = []
    for pair in pairs:
        doc = assign_text_to_document(load_document(pair.image_path, pair.json_path))
        warning_counts.append(len(doc.warnings))
        ok, errors = validate_docling_record(export_docling(doc), pair.image_path, check_png=True)
        assert ok, (pair.json_path.name, errors[:5])
    assert sum(warning_counts) > 0


def test_export_deduplicates_same_grid_key_same_text_cells():
    pair = discover_pairs(SAMPLE_IMAGE, SAMPLE_JSON)[0]
    doc = assign_text_to_document(load_document(pair.image_path, pair.json_path))
    duplicated = replace(doc, cells=(*doc.cells, doc.cells[0]))

    record = export_docling(duplicated)
    ok, errors = validate_docling_record(record, pair.image_path, check_png=True)

    assert ok, errors[:10]
    assert len(record["cells"]) == sum(1 for cell in doc.cells if cell.text != "")


def test_export_normalizes_oversized_source_table_bbox_to_crop_extent():
    pair = next(
        pair
        for pair in discover_pairs(SAMPLE_IMAGE, SAMPLE_JSON)
        if pair.stem == "GG7ZJMUOMWCUJTA5N6QC72CGLFRFCQJZ_0000"
    )
    doc = load_document(pair.image_path, pair.json_path)
    assert doc.table_bbox_px.width != doc.image_size[0]

    record = export_docling(doc)
    ok, errors = validate_docling_record(record, pair.image_path, check_png=True)

    assert record["table_bbox_px"] == [0.0, 0.0, 4001.0, 2250.0]
    assert ok, errors[:10]


def test_saved_review_json_omits_empty_text_cells_and_uses_val_byte_format(tmp_path):
    pair = next(
        pair
        for pair in discover_pairs(FINANCIAL_IMAGE, FINANCIAL_JSON)
        if pair.stem == "PZLCAAWKREULAW55IEYEY3U5CPOX6EVL_0001"
    )
    doc = load_document(pair.image_path, pair.json_path)
    assert any(cell.text == "" for cell in doc.cells)

    record = export_docling(doc)
    target = tmp_path / pair.json_path.name
    save_docling_json(doc, target)
    raw = target.read_bytes()
    canonical = json.dumps(json.loads(raw), ensure_ascii=False, indent=2).encode("utf-8")

    assert all(cell["text"] != "" for cell in record["cells"])
    assert raw == canonical
    assert not raw.endswith(b"\n")
