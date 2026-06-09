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
