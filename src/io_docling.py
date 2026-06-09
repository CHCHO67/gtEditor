"""Docling-compatible JSON IO for the table ground-truth editor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable, Mapping

from models import (
    GridAxis,
    GridWarning,
    Rect,
    TableCell,
    TableDocument,
    TextSpan,
    detect_crossing_warnings,
)

EXPECTED_TOP_KEYS = [
    "source_pdf",
    "page_no",
    "table_index",
    "global_index",
    "num_rows",
    "num_cols",
    "image_size",
    "table_bbox_px",
    "h_lines",
    "v_lines",
    "cells",
    "layout_tedss_score",
]

EXPECTED_CELL_KEYS = [
    "row",
    "col",
    "end_row",
    "end_col",
    "row_span",
    "col_span",
    "text",
    "is_column_header",
    "is_row_header",
    "is_row_section",
    "is_fillable",
    "bbox_px",
]

PROJECT_SCHEMA_VERSION = 1
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")
OUTPUT_BUCKETS = ("saved", "discarded")


@dataclass(frozen=True, slots=True)
class TablePair:
    """Matching image/json pair discovered by stem."""

    stem: str
    image_path: Path
    json_path: Path

    def __iter__(self):
        yield self.image_path
        yield self.json_path

    def __getitem__(self, index: int) -> Path:
        return (self.image_path, self.json_path)[index]

    def __len__(self) -> int:
        return 2


@dataclass(frozen=True, slots=True)
class InputDataset:
    """One input-data folder with image/json children and discovered pairs."""

    name: str
    root: Path
    image_dir: Path
    json_dir: Path
    pairs: tuple[TablePair, ...]


@dataclass(frozen=True, slots=True)
class OutputWriteResult:
    """Summary for one table pair written to Output_data."""

    tab_name: str
    bucket: str
    stem: str
    image_path: Path
    json_path: Path
    warnings: tuple[str, ...] = ()


def _read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return data


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _coerce_image_size(record: Mapping[str, Any], image_path: str | Path | None = None) -> tuple[int, int]:
    raw = record.get("image_size")
    if (
        isinstance(raw, list)
        and len(raw) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in raw)
    ):
        return int(raw[0]), int(raw[1])

    if image_path is not None:
        try:
            from PIL import Image  # type: ignore

            with Image.open(image_path) as image:
                width, height = image.size
            return int(width), int(height)
        except ImportError as exc:  # pragma: no cover - exercised only without Pillow
            raise ValueError("record has no valid image_size and Pillow is not installed") from exc

    raise ValueError(f"record has no valid image_size: {raw!r}")


def discover_pairs(image_dir: str | Path, json_dir: str | Path) -> list[TablePair]:
    """Discover image/json pairs whose filenames share the same stem."""

    image_root = Path(image_dir)
    json_root = Path(json_dir)
    images: dict[str, Path] = {}
    for ext in IMAGE_EXTENSIONS:
        for path in image_root.glob(f"*{ext}"):
            images.setdefault(path.stem, path)
    pairs: list[TablePair] = []
    for json_path in sorted(json_root.glob("*.json")):
        image_path = images.get(json_path.stem)
        if image_path is not None:
            pairs.append(TablePair(stem=json_path.stem, image_path=image_path, json_path=json_path))
    return pairs


def discover_input_dataset(input_data: str | Path, *, name: str | None = None) -> InputDataset:
    """Discover one Input_data folder containing image/ and json/ children."""

    root = Path(input_data)
    image_dir = root / "image"
    json_dir = root / "json"
    if not image_dir.is_dir() or not json_dir.is_dir():
        raise FileNotFoundError(f"{root} must contain image/ and json/ directories")
    pairs = tuple(discover_pairs(image_dir, json_dir))
    return InputDataset(name=name or root.name or "input", root=root, image_dir=image_dir, json_dir=json_dir, pairs=pairs)


def _looks_like_input_dataset(root: Path) -> bool:
    return (root / "image").is_dir() and (root / "json").is_dir()


def _discover_input_path(input_data: str | Path) -> list[InputDataset]:
    """Discover datasets from either one dataset folder or an Input_data parent."""

    root = Path(input_data)
    if _looks_like_input_dataset(root):
        return [discover_input_dataset(root)]

    children = [
        discover_input_dataset(child)
        for child in sorted(root.iterdir(), key=lambda path: path.name)
        if child.is_dir() and _looks_like_input_dataset(child)
    ] if root.is_dir() else []
    if children:
        return children

    return [discover_input_dataset(root)]


def discover_input_datasets(input_data: Iterable[str | Path]) -> list[InputDataset]:
    """Discover repeatable --input-data folders with stable, unique tab names.

    Each path may be either:
    - one dataset folder containing image/ and json/
    - an Input_data parent containing multiple such dataset folders
    """

    datasets = [dataset for path in input_data for dataset in _discover_input_path(path)]
    names = _unique_names([dataset.name for dataset in datasets])
    return [InputDataset(name=name, root=d.root, image_dir=d.image_dir, json_dir=d.json_dir, pairs=d.pairs) for name, d in zip(names, datasets)]


def legacy_input_dataset(image_dir: str | Path, json_dir: str | Path, *, name: str = "input") -> InputDataset:
    """Compatibility wrapper for legacy --image-dir/--json-dir callers."""

    pairs = tuple(discover_pairs(image_dir, json_dir))
    return InputDataset(name=name, root=Path(image_dir).parent, image_dir=Path(image_dir), json_dir=Path(json_dir), pairs=pairs)


def output_tab_dir(output_data: str | Path, tab_name: str) -> Path:
    return Path(output_data) / _safe_name(tab_name)


def _output_bucket_dir(output_data: str | Path, tab_name: str, bucket: str) -> Path:
    if bucket not in OUTPUT_BUCKETS:
        raise ValueError(f"output bucket must be one of {OUTPUT_BUCKETS}, got {bucket!r}")
    return output_tab_dir(output_data, tab_name) / bucket


def save_output_pair(
    document: TableDocument,
    pair: TablePair,
    output_data: str | Path,
    tab_name: str,
    *,
    bucket: str = "saved",
) -> OutputWriteResult:
    """Write image and validated Docling JSON under Output_data/tab/bucket/image,json."""

    bucket_dir = _output_bucket_dir(output_data, tab_name, bucket)
    image_target = bucket_dir / "image" / pair.image_path.name
    json_target = bucket_dir / "json" / pair.json_path.name
    image_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pair.image_path, image_target)
    save_docling_json(document, json_target)
    ok, errors = validate_docling_record(document, image_target, check_png=True)
    if not ok:
        raise ValueError(f"exported JSON failed validation for {pair.stem}: {errors[:5]}")
    return OutputWriteResult(tab_name=tab_name, bucket=bucket, stem=pair.stem, image_path=image_target, json_path=json_target)


def save_output_tab(
    dataset: InputDataset,
    output_data: str | Path,
    *,
    documents: Mapping[str, TableDocument] | None = None,
    bucket: str = "saved",
) -> list[OutputWriteResult]:
    """Write every pair from one tab, using edited docs when supplied."""

    docs = documents or {}
    results: list[OutputWriteResult] = []
    for pair in dataset.pairs:
        document = docs.get(pair.stem)
        if document is None:
            document = load_document(pair.image_path, pair.json_path)
        results.append(save_output_pair(document, pair, output_data, dataset.name, bucket=bucket))
    return results


def verify_output_tab_counts(output_data: str | Path, dataset: InputDataset) -> tuple[bool, str]:
    tab_dir = output_tab_dir(output_data, dataset.name)
    bucket_counts: dict[str, tuple[int, int]] = {}
    for bucket in OUTPUT_BUCKETS:
        bucket_dir = tab_dir / bucket
        image_dir = bucket_dir / "image"
        json_dir = bucket_dir / "json"
        image_count = sum(1 for path in image_dir.iterdir() if path.is_file()) if image_dir.is_dir() else 0
        json_count = sum(1 for path in json_dir.glob("*.json")) if json_dir.is_dir() else 0
        bucket_counts[bucket] = (image_count, json_count)
    image_count = sum(counts[0] for counts in bucket_counts.values())
    json_count = sum(counts[1] for counts in bucket_counts.values())
    expected = len(dataset.pairs)
    ok = expected == image_count == json_count
    details = " ".join(f"{bucket}=images:{counts[0]},json:{counts[1]}" for bucket, counts in bucket_counts.items())
    return ok, f"{dataset.name}: input={expected} output_images={image_count} output_json={json_count} {details}"


def _safe_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return clean.strip("._") or "input"


def _unique_names(names: Iterable[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for name in names:
        base = _safe_name(name)
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.append(base if count == 0 else f"{base}-{count + 1}")
    return result


def load_document(
    image_path: str | Path,
    json_path: str | Path,
    *,
    min_line_gap: float = 0.0,
) -> TableDocument:
    """Load a Docling-compatible table JSON plus matching crop image path."""

    image = Path(image_path)
    json_file = Path(json_path)
    record = _read_json(json_file)
    width, height = _coerce_image_size(record, image)
    num_rows = _int_field(record, "num_rows", default=0)
    num_cols = _int_field(record, "num_cols", default=0)

    row_axis = GridAxis.from_line_dict(
        name="horizontal",
        size=height,
        declared_count=num_rows,
        lines=record.get("h_lines") if isinstance(record.get("h_lines"), Mapping) else {},
        min_gap=min_line_gap,
    )
    col_axis = GridAxis.from_line_dict(
        name="vertical",
        size=width,
        declared_count=num_cols,
        lines=record.get("v_lines") if isinstance(record.get("v_lines"), Mapping) else {},
        min_gap=min_line_gap,
    )

    cells: list[TableCell] = []
    spans: list[TextSpan] = []
    raw_cells = record.get("cells", [])
    if not isinstance(raw_cells, list):
        raise TypeError("record['cells'] must be a list")
    for idx, raw_cell in enumerate(raw_cells):
        if not isinstance(raw_cell, Mapping):
            raise TypeError(f"cell[{idx}] must be an object")
        cell = _cell_from_docling(raw_cell, idx)
        span_id = f"cell-{idx}"
        span = TextSpan(
            span_id=span_id,
            text=cell.text,
            bbox=cell.source_bbox if cell.source_bbox is not None else Rect(0, 0, 0, 0),
            source_cell_index=idx,
            assigned_cell_key=cell.key,
            metadata={"source_cell": idx},
        )
        cells.append(_replace_assigned(cell, (span_id,)))
        spans.append(span)

    warnings = detect_crossing_warnings(spans, row_axis, col_axis)
    return TableDocument(
        stem=json_file.stem,
        source_pdf=str(record.get("source_pdf", "")),
        page_no=_int_field(record, "page_no", default=1),
        table_index=_int_field(record, "table_index", default=0),
        global_index=_int_field(record, "global_index", default=0),
        image_size=(width, height),
        table_bbox_px=Rect.from_sequence(record.get("table_bbox_px", [0, 0, width, height])),
        row_axis=row_axis,
        col_axis=col_axis,
        cells=tuple(cells),
        text_spans=tuple(spans),
        warnings=warnings,
        layout_tedss_score=_optional_float(record.get("layout_tedss_score")),
        image_path=str(image),
        json_path=str(json_file),
        metadata={
            "source_top_keys": list(record.keys()),
            "source_cell_count": len(raw_cells),
        },
    )


def load_documents(image_dir: str | Path, json_dir: str | Path) -> list[TableDocument]:
    return [load_document(pair.image_path, pair.json_path) for pair in discover_pairs(image_dir, json_dir)]


def _export_cells(document: TableDocument) -> list[dict[str, Any]]:
    """Serialize cells while removing exact same-key/same-text duplicates.

    Line add/delete/merge experiments can leave duplicate logical cells with the
    same grid span and same text.  The validator treats those as a failed dedup,
    so collapse only those exact duplicates.  Same-span cells with different
    text are preserved because they represent a genuine grid collision.
    """

    cells: list[dict[str, Any]] = []
    by_key_text: dict[tuple[tuple[int, int, int, int], str], int] = {}
    bool_fields = ("is_column_header", "is_row_header", "is_row_section", "is_fillable")
    for cell in document.cells:
        key = (cell.key, cell.text)
        bbox = document.cell_bbox(cell)
        payload = cell.to_docling(bbox=bbox)
        existing_index = by_key_text.get(key)
        if existing_index is None:
            by_key_text[key] = len(cells)
            cells.append(payload)
            continue
        existing = cells[existing_index]
        for field in bool_fields:
            existing[field] = bool(existing.get(field)) or bool(payload.get(field))
    return cells


def export_docling(document: TableDocument) -> dict[str, Any]:
    """Serialize a document to the exact key order expected by tte_validate.py."""

    cells = _export_cells(document)
    return {
        "source_pdf": document.source_pdf,
        "page_no": document.page_no,
        "table_index": document.table_index,
        "global_index": document.global_index,
        "num_rows": document.num_rows,
        "num_cols": document.num_cols,
        "image_size": [document.image_size[0], document.image_size[1]],
        "table_bbox_px": document.table_bbox_px.to_list(),
        "h_lines": document.row_axis.to_line_dict(),
        "v_lines": document.col_axis.to_line_dict(),
        "cells": cells,
        "layout_tedss_score": document.layout_tedss_score,
    }


def save_docling_json(document: TableDocument, path: str | Path) -> None:
    _write_json(path, export_docling(document))


def document_to_project_state(document: TableDocument) -> dict[str, Any]:
    """Serialize editor/audit state to project-side `.gt.json`."""

    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "editor": {
            "name": "gt_editor",
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
        "source": {
            "stem": document.stem,
            "image_path": document.image_path,
            "json_path": document.json_path,
            "source_pdf": document.source_pdf,
            "page_no": document.page_no,
            "table_index": document.table_index,
            "global_index": document.global_index,
        },
        "image_size": [document.image_size[0], document.image_size[1]],
        "table_bbox_px": document.table_bbox_px.to_list(),
        "num_rows": document.num_rows,
        "num_cols": document.num_cols,
        "h_lines": document.row_axis.to_line_dict(),
        "v_lines": document.col_axis.to_line_dict(),
        "computed_edges": {
            "horizontal": list(document.row_axis.full_edges),
            "vertical": list(document.col_axis.full_edges),
        },
        "cells": document.to_state_cells(),
        "text_spans": [span.to_state() for span in document.text_spans],
        "warnings": [warning.to_state() for warning in document.warnings],
        "layout_tedss_score": document.layout_tedss_score,
        "metadata": dict(document.metadata),
    }


def save_project_state(document: TableDocument, path: str | Path) -> None:
    _write_json(path, document_to_project_state(document))


def load_project_state(path: str | Path) -> TableDocument:
    """Load a `.gt.json` project state written by `save_project_state`."""

    state = _read_json(path)
    if state.get("schema_version") != PROJECT_SCHEMA_VERSION:
        raise ValueError(f"unsupported project schema_version: {state.get('schema_version')!r}")
    source = state.get("source")
    if not isinstance(source, Mapping):
        raise TypeError("project state missing source object")
    width, height = _state_image_size(state)
    row_axis = GridAxis.from_line_dict(
        name="horizontal",
        size=height,
        declared_count=_int_field(state, "num_rows", default=0),
        lines=state.get("h_lines") if isinstance(state.get("h_lines"), Mapping) else {},
    )
    col_axis = GridAxis.from_line_dict(
        name="vertical",
        size=width,
        declared_count=_int_field(state, "num_cols", default=0),
        lines=state.get("v_lines") if isinstance(state.get("v_lines"), Mapping) else {},
    )
    cells = tuple(_cell_from_state(item) for item in _list_field(state, "cells"))
    text_spans = tuple(_span_from_state(item) for item in _list_field(state, "text_spans"))
    warnings = tuple(_warning_from_state(item) for item in _list_field(state, "warnings"))
    return TableDocument(
        stem=str(source.get("stem") or Path(str(path)).stem.removesuffix(".gt")),
        source_pdf=str(source.get("source_pdf", "")),
        page_no=_source_int(source, "page_no", default=1),
        table_index=_source_int(source, "table_index", default=0),
        global_index=_source_int(source, "global_index", default=0),
        image_size=(width, height),
        table_bbox_px=Rect.from_sequence(state.get("table_bbox_px", [0, 0, width, height])),
        row_axis=row_axis,
        col_axis=col_axis,
        cells=cells,
        text_spans=text_spans,
        warnings=warnings,
        layout_tedss_score=_optional_float(state.get("layout_tedss_score")),
        image_path=_optional_str(source.get("image_path")),
        json_path=_optional_str(source.get("json_path")),
        metadata=state.get("metadata") if isinstance(state.get("metadata"), Mapping) else {},
    )


def validate_docling_record(
    record_or_document: Mapping[str, Any] | TableDocument,
    png_path: str | Path | None = None,
    *,
    check_png: bool = True,
) -> tuple[bool, list[str]]:
    """Validate an exported Docling-style record with the bundled validator."""

    record = export_docling(record_or_document) if isinstance(record_or_document, TableDocument) else dict(record_or_document)
    from docling_validator import validate

    return validate(record, str(png_path) if png_path is not None else None, check_png=check_png)


def _cell_from_docling(raw: Mapping[str, Any], index: int) -> TableCell:
    bbox = raw.get("bbox_px")
    source_bbox = Rect.from_sequence(bbox) if isinstance(bbox, list) and len(bbox) == 4 else None
    row = _source_int(raw, "row", default=0)
    col = _source_int(raw, "col", default=0)
    end_row = _source_int(raw, "end_row", default=row + max(1, _source_int(raw, "row_span", default=1)))
    end_col = _source_int(raw, "end_col", default=col + max(1, _source_int(raw, "col_span", default=1)))
    return TableCell(
        row=row,
        col=col,
        end_row=end_row,
        end_col=end_col,
        text=str(raw.get("text", "")),
        is_column_header=bool(raw.get("is_column_header", False)),
        is_row_header=bool(raw.get("is_row_header", False)),
        is_row_section=bool(raw.get("is_row_section", False)),
        is_fillable=bool(raw.get("is_fillable", False)),
        source_bbox=source_bbox,
        metadata={"source_cell_index": index},
    )


def _cell_from_state(raw: Any) -> TableCell:
    if not isinstance(raw, Mapping):
        raise TypeError(f"state cell must be an object, got {type(raw).__name__}")
    source_bbox_raw = raw.get("source_bbox")
    source_bbox = Rect.from_sequence(source_bbox_raw) if isinstance(source_bbox_raw, list) and len(source_bbox_raw) == 4 else None
    assigned_raw = raw.get("assigned_span_ids", [])
    assigned = tuple(str(item) for item in assigned_raw) if isinstance(assigned_raw, list) else ()
    return TableCell(
        row=_source_int(raw, "row", default=0),
        col=_source_int(raw, "col", default=0),
        end_row=_source_int(raw, "end_row", default=1),
        end_col=_source_int(raw, "end_col", default=1),
        text=str(raw.get("text", "")),
        is_column_header=bool(raw.get("is_column_header", False)),
        is_row_header=bool(raw.get("is_row_header", False)),
        is_row_section=bool(raw.get("is_row_section", False)),
        is_fillable=bool(raw.get("is_fillable", False)),
        source_bbox=source_bbox,
        assigned_span_ids=assigned,
        metadata=raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {},
    )


def _span_from_state(raw: Any) -> TextSpan:
    if not isinstance(raw, Mapping):
        raise TypeError(f"state text span must be an object, got {type(raw).__name__}")
    assigned = raw.get("assigned_cell_key")
    assigned_key = tuple(int(v) for v in assigned) if isinstance(assigned, list) and len(assigned) == 4 else None
    return TextSpan(
        span_id=str(raw.get("span_id", "")),
        text=str(raw.get("text", "")),
        bbox=Rect.from_sequence(raw.get("bbox", [0, 0, 0, 0])),
        source_cell_index=raw.get("source_cell_index") if isinstance(raw.get("source_cell_index"), int) else None,
        assigned_cell_key=assigned_key,  # type: ignore[arg-type]
        metadata=raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {},
    )


def _warning_from_state(raw: Any) -> GridWarning:
    if not isinstance(raw, Mapping):
        raise TypeError(f"state warning must be an object, got {type(raw).__name__}")
    axis = raw.get("axis")
    candidate = raw.get("candidate")
    if axis not in ("horizontal", "vertical"):
        raise ValueError(f"invalid warning axis: {axis!r}")
    if candidate not in ("left_right_split", "top_bottom_split"):
        raise ValueError(f"invalid warning candidate: {candidate!r}")
    return GridWarning(
        warning_id=str(raw.get("warning_id", "")),
        axis=axis,
        line_key=_source_int(raw, "line_key", default=0),
        line_coordinate=float(raw.get("line_coordinate", 0.0)),
        span_id=str(raw.get("span_id", "")),
        source_cell_index=raw.get("source_cell_index") if isinstance(raw.get("source_cell_index"), int) else None,
        candidate=candidate,
        bbox=Rect.from_sequence(raw.get("bbox", [0, 0, 0, 0])),
        message=str(raw.get("message", "")),
    )


def _replace_assigned(cell: TableCell, assigned_span_ids: tuple[str, ...]) -> TableCell:
    return TableCell(
        row=cell.row,
        col=cell.col,
        end_row=cell.end_row,
        end_col=cell.end_col,
        text=cell.text,
        is_column_header=cell.is_column_header,
        is_row_header=cell.is_row_header,
        is_row_section=cell.is_row_section,
        is_fillable=cell.is_fillable,
        source_bbox=cell.source_bbox,
        assigned_span_ids=assigned_span_ids,
        metadata=cell.metadata,
    )


def _int_field(mapping: Mapping[str, Any], key: str, *, default: int) -> int:
    return _source_int(mapping, key, default=default)


def _source_int(mapping: Mapping[str, Any], key: str, *, default: int) -> int:
    value = mapping.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _state_image_size(state: Mapping[str, Any]) -> tuple[int, int]:
    raw = state.get("image_size")
    if (
        isinstance(raw, list)
        and len(raw) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in raw)
    ):
        return int(raw[0]), int(raw[1])
    raise ValueError(f"project state has invalid image_size: {raw!r}")


def _list_field(mapping: Mapping[str, Any], key: str) -> Iterable[Any]:
    value = mapping.get(key, [])
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return value


# Backwards-compatible public aliases used by scripts/GUI/tests.
read_json = _read_json
write_json = _write_json
save_docling = save_docling_json
project_state = document_to_project_state

def iter_documents(image_dir: str | Path, json_dir: str | Path):
    for pair in discover_pairs(image_dir, json_dir):
        yield load_document(pair.image_path, pair.json_path)
