from __future__ import annotations

import os
import shutil
from pathlib import Path

from gt_editor.cli import main
from gt_editor.io_docling import (
    discover_input_datasets,
    discover_pairs,
    load_document,
    save_output_pair,
    save_output_tab,
    validate_docling_record,
    verify_output_tab_counts,
)

SAMPLE_IMAGE = Path("gt_editor_samples/image")
SAMPLE_JSON = Path("gt_editor_samples/json")


def _make_input_data(root: Path, stems: list[str]) -> Path:
    image_dir = root / "image"
    json_dir = root / "json"
    image_dir.mkdir(parents=True)
    json_dir.mkdir(parents=True)
    for stem in stems:
        image = next(SAMPLE_IMAGE.glob(f"{stem}.*"))
        shutil.copy2(image, image_dir / image.name)
        shutil.copy2(SAMPLE_JSON / f"{stem}.json", json_dir / f"{stem}.json")
    return root


def _sample_stems(count: int) -> list[str]:
    return [pair.stem for pair in discover_pairs(SAMPLE_IMAGE, SAMPLE_JSON)[:count]]


def test_repeatable_input_data_gets_unique_tab_names(tmp_path: Path):
    stems = _sample_stems(1)
    first = _make_input_data(tmp_path / "Input data", stems)
    second = _make_input_data(tmp_path / "nested" / "Input data", stems)

    datasets = discover_input_datasets([first, second])

    assert [dataset.name for dataset in datasets] == ["Input_data", "Input_data-2"]
    assert [len(dataset.pairs) for dataset in datasets] == [1, 1]


def test_save_current_and_tab_write_valid_image_json_layout(tmp_path: Path):
    input_data = _make_input_data(tmp_path / "Input data", _sample_stems(2))
    dataset = discover_input_datasets([input_data])[0]
    output_data = tmp_path / "Output_data"

    first_pair = dataset.pairs[0]
    document = load_document(first_pair.image_path, first_pair.json_path)
    current = save_output_pair(document, first_pair, output_data, dataset.name)
    assert current.image_path.exists()
    assert current.json_path.exists()
    ok, errors = validate_docling_record(document, current.image_path, check_png=True)
    assert ok, errors

    results = save_output_tab(dataset, output_data)
    assert len(results) == 2
    ok, counts = verify_output_tab_counts(output_data, dataset)
    assert ok, counts
    assert len(list((output_data / dataset.name / "image").iterdir())) == 2
    assert len(list((output_data / dataset.name / "json").glob("*.json"))) == 2


def test_cli_save_all_repeatable_input_data(tmp_path: Path, capsys):
    stems = _sample_stems(2)
    first = _make_input_data(tmp_path / "Input data", stems[:1])
    second = _make_input_data(tmp_path / "nested" / "Input data", stems[1:])
    output_data = tmp_path / "Output_data"

    code = main([
        "--input-data",
        str(first),
        "--input-data",
        str(second),
        "--output-data",
        str(output_data),
        "--save-all",
    ])

    assert code == 0
    out = capsys.readouterr().out
    assert "save-all ok tabs=2 pairs=2" in out
    assert (output_data / "Input_data" / "image").is_dir()
    assert (output_data / "Input_data" / "json").is_dir()
    assert (output_data / "Input_data-2" / "image").is_dir()
    assert (output_data / "Input_data-2" / "json").is_dir()


def test_gui_tabs_and_save_actions_offscreen(tmp_path: Path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from gt_editor.app import build_app

    first = _make_input_data(tmp_path / "tab-one", _sample_stems(1))
    second = _make_input_data(tmp_path / "tab-two", _sample_stems(1))
    output_data = tmp_path / "Output_data"

    app, win = build_app(input_data=[first, second], output_data=output_data)
    try:
        assert win.tabs.count() == 2
        assert win.doc is not None
        assert win.sessions[0].status_tabs.count() == 3
        assert win.sessions[0].status_tabs.tabText(0).startswith("검토 (1)")
        assert win.save_button.text().startswith("Save")
        assert win.discard_button.text().startswith("Discard")
        assert any(button.text().startswith("Move Line") for button in win.edit_buttons)
        assert any(button.text().startswith("Add V") for button in win.edit_buttons)
        assert any(button.text().startswith("Delete  D") for button in win.edit_buttons)
        assert any(button.text().startswith("Merge  1") for button in win.edit_buttons)
        assert any(button.text().startswith("Unmerge  2") for button in win.edit_buttons)

        win.save_current()
        assert win.sessions[0].status_tabs.tabText(0).startswith("검토 (0)")
        assert win.sessions[0].status_tabs.tabText(1).startswith("검토 완료 (1)")
        assert len(list((output_data / "tab-one" / "image").iterdir())) == 1
        assert len(list((output_data / "tab-one" / "json").glob("*.json"))) == 1

        win.tabs.setCurrentIndex(1)
        assert win.doc is not None
        win.discard_current()
        assert win.sessions[1].status_tabs.tabText(0).startswith("검토 (0)")
        assert win.sessions[1].status_tabs.tabText(2).startswith("버리기 (1)")
        assert len(list((output_data / "tab-two" / "image").iterdir())) == 1
        assert len(list((output_data / "tab-two" / "json").glob("*.json"))) == 1
    finally:
        win.close()
        app.processEvents()


def test_gui_edit_tools_line_drag_and_merge_visuals_offscreen(tmp_path: Path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from gt_editor.app import build_app

    first = _make_input_data(tmp_path / "tab-one", _sample_stems(1))
    output_data = tmp_path / "Output_data"

    app, win = build_app(input_data=[first], output_data=output_data)
    try:
        assert win.doc is not None
        assert any(button.text().startswith("Move Line") for button in win.edit_buttons)
        assert any(button.text().startswith("Select Cells") for button in win.edit_buttons)
        assert any(button.text().startswith("Add V") for button in win.edit_buttons)
        assert any(button.text().startswith("Merge") for button in win.edit_buttons)

        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtWidgets import QGraphicsSimpleTextItem, QGraphicsView

        win.activate_cell_select_mode()
        assert win.view.dragMode() == QGraphicsView.RubberBandDrag
        assert win.view._cell_drag_selection_enabled
        win.activate_line_move_mode()
        assert win.view.dragMode() == QGraphicsView.NoDrag
        assert not win.view._cell_drag_selection_enabled

        assert not [item for item in win.scene.items() if isinstance(item, QGraphicsSimpleTextItem)]
        text_box_items = [item for item in win.scene.items() if item.data(0) == "text_box"]
        assert text_box_items
        assert all(item.brush().color().green() > item.brush().color().red() for item in text_box_items[:5])
        assert all(20 <= item.brush().color().alpha() <= 90 for item in text_box_items[:5])

        vertical_line = next(item for item in win.scene.items() if hasattr(item, "axis") and item.axis == "x")
        vertical_color = vertical_line.pen().color()
        assert vertical_color.red() > vertical_color.green()
        assert vertical_color.red() > vertical_color.blue()

        from gt_editor.commands import CommandError, MergeCellsCommand

        cell_items = [item for item in win.scene.items() if hasattr(item, "cell")]
        cells_by_key = {(item.cell.row, item.cell.col): item for item in cell_items if item.cell.row_span == 1 and item.cell.col_span == 1}
        merge_target = None
        for (row, col), left_item in cells_by_key.items():
            right_item = cells_by_key.get((row, col + 1))
            if right_item is None:
                continue
            try:
                MergeCellsCommand(selection=[win.doc.cells[left_item.cell_index], win.doc.cells[right_item.cell_index]]).apply(win.doc)
            except CommandError:
                continue
            merge_target = (row, col, left_item, right_item)
            break
        assert merge_target is not None
        row, col, left_item, right_item = merge_target
        win.activate_cell_select_mode()
        win.scene.clearSelection()
        drag_rect = QRectF(left_item.sceneBoundingRect().center(), right_item.sceneBoundingRect().center())
        win.view.select_cells_in_scene_rect(drag_rect)
        assert {left_item.cell_index, right_item.cell_index}.issubset(set(win.selected_cells()))
        win.merge_selected_cells()
        app.processEvents()
        merged_item = next(
            item
            for item in win.scene.items()
            if hasattr(item, "cell")
            and item.cell.row == row
            and item.cell.col == col
            and item.cell.end_row == row + 1
            and item.cell.end_col == col + 2
        )
        assert merged_item.brush().color().alpha() > 40
        assert merged_item.brush().color().blue() > merged_item.brush().color().green()
        win.scene.clearSelection()
        merged_item.setSelected(True)
        win.unmerge_selected_cell()
        app.processEvents()
        assert not [
            item
            for item in win.scene.items()
            if hasattr(item, "cell")
            and item.cell.row == row
            and item.cell.col == col
            and item.cell.end_row == row + 1
            and item.cell.end_col == col + 2
        ]
        assert (row, col) in {(item.cell.row, item.cell.col) for item in win.scene.items() if hasattr(item, "cell")}
        assert (row, col + 1) in {(item.cell.row, item.cell.col) for item in win.scene.items() if hasattr(item, "cell")}

        edges = [float(value) for value in win.doc.x_edges]
        gap_index = max(range(len(edges) - 1), key=lambda idx: edges[idx + 1] - edges[idx])
        cursor_x = (edges[gap_index] + edges[gap_index + 1]) / 2.0
        before_cols = win.doc.num_cols
        win._cursor_scene_point = lambda: QPointF(cursor_x, float(win.doc.height) / 2.0)
        win.add_line("x")
        assert win.doc.num_cols == before_cols + 1
        assert any(abs(float(value) - cursor_x) < 0.01 for value in win.doc.x_edges)

        line = next(item for item in win.scene.items() if hasattr(item, "axis") and item.axis == "x")
        edge_index = line.edge_index
        before = float(win.doc.x_edges[edge_index])
        next_gap = float(win.doc.x_edges[edge_index + 1]) - before
        prev_gap = before - float(win.doc.x_edges[edge_index - 1])
        delta = 3.0 if next_gap > 8.0 else -3.0 if prev_gap > 8.0 else 1.0
        line.setPos(delta, 0.0)
        app.processEvents()
        assert abs(float(win.doc.x_edges[edge_index]) - before) < 0.01
        line.commit_pending_move()
        app.processEvents()
        assert abs(float(win.doc.x_edges[edge_index]) - (before + delta)) < 0.01
        assert win.stack is not None and win.stack.can_undo
        win.undo()
        assert abs(float(win.doc.x_edges[edge_index]) - before) < 0.01
    finally:
        win.close()
        app.processEvents()
