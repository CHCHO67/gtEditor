from gt_editor.io_docling import discover_pairs, export_docling, load_document, validate_docling_record
from gt_editor.text_assign import assign_text_to_document


def test_all_selected_samples_export_validate():
    pairs = discover_pairs("gt_editor_samples/image", "gt_editor_samples/json")
    assert len(pairs) == 10
    warning_counts = []
    for pair in pairs:
        doc = assign_text_to_document(load_document(pair.image_path, pair.json_path))
        warning_counts.append(len(doc.warnings))
        ok, errors = validate_docling_record(export_docling(doc), pair.image_path, check_png=True)
        assert ok, (pair.json_path.name, errors[:5])
    assert sum(warning_counts) > 0
