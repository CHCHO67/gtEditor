"""Undoable table-structure editing commands for the GT editor.

The command layer is deliberately model-light: it operates on either future
``models`` dataclasses or existing Docling-compatible dictionaries.
Each command returns an updated document copy from ``apply(document)`` and stores
a snapshot so ``revert()`` can be used by the GUI undo stack.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
import copy
import math
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from text_assign import (
    _cell_id,
    _get,
    _replace_record,
    assign_text_to_document,
    reconstruct_cell_text,
    update_cell_geometry,
)

Number = int | float


def _is_table_document(document: Any) -> bool:
    return hasattr(document, "row_axis") and hasattr(document, "col_axis") and hasattr(document, "cells")


class CommandError(ValueError):
    """Raised when an edit would violate table-grid invariants."""


def clone_document(document: Any) -> Any:
    return copy.deepcopy(document)


def _is_mapping(obj: Any) -> bool:
    return isinstance(obj, MutableMapping) or isinstance(obj, Mapping)


def _has_field(obj: Any, name: str) -> bool:
    if _is_mapping(obj):
        return name in obj
    return hasattr(obj, name)


def _set(obj: Any, name: str, value: Any) -> None:
    if isinstance(obj, MutableMapping):
        obj[name] = value
    else:
        setattr(obj, name, value)


def _dict_line_values(lines: Mapping[str, Number]) -> list[float]:
    return [float(lines[k]) for k in sorted(lines, key=lambda item: int(item))]


def _image_size(document: Any) -> tuple[float, float] | None:
    image_size = _get(document, "image_size", None)
    if isinstance(image_size, Sequence) and len(image_size) == 2:
        return float(image_size[0]), float(image_size[1])
    return None


def _normalize_lines(lines: Iterable[Number], *, min_gap: float = 0.0) -> list[float]:
    values = sorted(float(v) for v in lines if math.isfinite(float(v)))
    if not values:
        raise CommandError("axis must contain at least one finite coordinate")
    normalized: list[float] = []
    for value in values:
        if normalized and value - normalized[-1] < min_gap:
            continue
        normalized.append(value)
    return normalized


def get_axis(document: Any, axis: str) -> list[float]:
    """Return full boundary-inclusive axis lines for ``axis`` (``x`` or ``y``)."""

    axis = _axis(axis)
    if _is_table_document(document):
        axis_obj = _get(document, "col_axis") if axis == "x" else _get(document, "row_axis")
        return [float(v) for v in axis_obj.full_edges]
    primary = "x_lines" if axis == "x" else "y_lines"
    docling = "v_lines" if axis == "x" else "h_lines"
    if _has_field(document, primary):
        values = _get(document, primary)
        if isinstance(values, Mapping):
            return _dict_line_values(values)
        return [float(v) for v in values]

    if _has_field(document, docling):
        interior = _get(document, docling) or {}
        if isinstance(interior, Mapping):
            inner = _dict_line_values(interior)
        else:
            inner = [float(v) for v in interior]
        size = _image_size(document)
        if size is None:
            if inner:
                return _normalize_lines(inner)
            raise CommandError(f"cannot infer {axis}-axis boundaries without image_size")
        limit = size[0] if axis == "x" else size[1]
        return _normalize_lines([0.0, *inner, float(limit)])

    raise CommandError(f"document has no {primary!r} or {docling!r} axis")


def set_axis_on_copy(document: Any, axis: str, lines: Sequence[Number]) -> Any:
    """Return a document copy with one axis updated."""

    axis = _axis(axis)
    values = [float(v) for v in lines]
    if _is_table_document(document):
        from models import GridAxis
        if len(values) < 2:
            raise CommandError("axis needs at least two boundary values")
        if axis == "x":
            new_axis = GridAxis.from_interior_values(name="vertical", size=int(_get(document, "image_size")[0]), interior_values=values[1:-1], min_gap=0.0)
            return replace(document, col_axis=new_axis)
        new_axis = GridAxis.from_interior_values(name="horizontal", size=int(_get(document, "image_size")[1]), interior_values=values[1:-1], min_gap=0.0)
        return replace(document, row_axis=new_axis)
    primary = "x_lines" if axis == "x" else "y_lines"
    docling = "v_lines" if axis == "x" else "h_lines"
    count_field = "num_cols" if axis == "x" else "num_rows"

    updates: dict[str, Any] = {count_field: max(0, len(values) - 1)}
    if _has_field(document, primary):
        updates[primary] = values
    elif _has_field(document, docling):
        updates[docling] = {str(index): coord for index, coord in enumerate(values[1:-1])}
    else:
        updates[primary] = values
    return _replace_record(document, **updates)


def set_axes_on_copy(document: Any, x_lines: Sequence[Number], y_lines: Sequence[Number]) -> Any:
    updated = set_axis_on_copy(document, "x", x_lines)
    return set_axis_on_copy(updated, "y", y_lines)


def get_cells(document: Any) -> list[Any]:
    cells = _get(document, "cells", None)
    if cells is None:
        raise CommandError("document has no cells")
    return list(cells)


def set_cells_on_copy(document: Any, cells: Sequence[Any]) -> Any:
    if _is_table_document(document):
        return _replace_record(document, cells=tuple(cells))
    return _replace_record(document, cells=list(cells))


def _axis(axis: str) -> str:
    normalized = axis.lower()
    if normalized in ("x", "v", "vertical", "col", "column"):
        return "x"
    if normalized in ("y", "h", "horizontal", "row"):
        return "y"
    raise CommandError(f"axis must be x/y or vertical/horizontal, got {axis!r}")


def _line_index(lines: Sequence[Number], line_index: int) -> int:
    index = int(line_index)
    if index <= 0 or index >= len(lines) - 1:
        raise CommandError(f"line index {line_index} is not an interior line")
    return index


def clamp_line_coordinate(lines: Sequence[Number], index: int, coordinate: Number, *, min_gap: float) -> float:
    prev_value = float(lines[index - 1]) + float(min_gap)
    next_value = float(lines[index + 1]) - float(min_gap)
    if prev_value > next_value:
        raise CommandError(f"line {index} cannot maintain min_gap={min_gap}")
    return min(max(float(coordinate), prev_value), next_value)


def _remap_cell_axis_for_add(cell: Any, axis: str, insert_index: int) -> list[Any]:
    row_name, end_name = ("col", "end_col") if axis == "x" else ("row", "end_row")
    start = int(_get(cell, row_name))
    end = int(_get(cell, end_name))
    split_band = insert_index - 1

    # Existing cell does not touch the split band.
    if end <= split_band:
        return [cell]
    if start > split_band:
        return [_replace_record(cell, **{row_name: start + 1, end_name: end + 1})]

    # The new line runs through this cell's covered band.  Split the cell into
    # two pieces so text assignment can redistribute content instead of silently
    # preserving a stale merged span.
    mapped_end = end + 1
    first_updates = {end_name: insert_index}
    second_updates = {row_name: insert_index, end_name: mapped_end}
    first = _replace_record(cell, **first_updates)
    second = _replace_record(cell, **second_updates)
    return [first, second]


def _boundary_start_after_delete(start: int, delete_index: int) -> int:
    return start if start < delete_index else start - 1


def _boundary_end_after_delete(end: int, delete_index: int) -> int:
    return end if end <= delete_index else end - 1


def _remap_cell_axis_for_delete(cell: Any, axis: str, delete_index: int) -> Any:
    row_name, end_name = ("col", "end_col") if axis == "x" else ("row", "end_row")
    start = int(_get(cell, row_name))
    end = int(_get(cell, end_name))
    new_start = _boundary_start_after_delete(start, delete_index)
    new_end = _boundary_end_after_delete(end, delete_index)
    if new_end <= new_start:
        new_end = new_start + 1
    return _replace_record(cell, **{row_name: new_start, end_name: new_end})


def _sort_cells(cells: Sequence[Any]) -> list[Any]:
    return sorted(cells, key=lambda c: (int(_get(c, "row", 0)), int(_get(c, "col", 0)), int(_get(c, "end_row", 0)), int(_get(c, "end_col", 0))))


def _merge_duplicate_cells(cells: Sequence[Any]) -> list[Any]:
    grouped: dict[tuple[int, int, int, int], list[Any]] = {}
    for cell in cells:
        key = (int(_get(cell, "row")), int(_get(cell, "col")), int(_get(cell, "end_row")), int(_get(cell, "end_col")))
        grouped.setdefault(key, []).append(cell)

    merged: list[Any] = []
    for key, group in grouped.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        base = group[0]
        text_parts = [str(_get(cell, "text", "") or "").strip() for cell in _sort_cells(group)]
        source_span_ids: list[str] = []
        for cell in group:
            ids = _get(cell, "source_span_ids", []) or []
            source_span_ids.extend(str(item) for item in ids)
        updates = {
            "text": " ".join(part for part in text_parts if part),
            "is_column_header": any(bool(_get(cell, "is_column_header", False)) for cell in group),
            "is_row_header": any(bool(_get(cell, "is_row_header", False)) for cell in group),
            "is_row_section": any(bool(_get(cell, "is_row_section", False)) for cell in group),
            "is_fillable": any(bool(_get(cell, "is_fillable", False)) for cell in group),
            "source_span_ids": list(dict.fromkeys(source_span_ids)),
        }
        merged.append(_replace_record(base, **updates))
    return _sort_cells(merged)


def _refresh_cell_geometry(cells: Sequence[Any], x_lines: Sequence[Number], y_lines: Sequence[Number]) -> list[Any]:
    return [update_cell_geometry(cell, x_lines, y_lines) for cell in _sort_cells(cells)]


def _refresh_text(document: Any, *, recompute_text: bool) -> Any:
    if not recompute_text:
        return document
    try:
        return assign_text_to_document(document)
    except Exception:
        # The command layer must stay usable while other workers are still
        # building models/io. Geometry is already refreshed; callers can rerun
        # text assignment once the full document model is available.
        return document


def move_line(
    document: Any,
    axis: str,
    line_index: int,
    coordinate: Number,
    *,
    min_gap: float = 2.0,
    recompute_text: bool = True,
) -> Any:
    """Move an interior grid line, clamped between neighboring lines."""

    axis = _axis(axis)
    x_lines = get_axis(document, "x")
    y_lines = get_axis(document, "y")
    lines = x_lines if axis == "x" else y_lines
    index = _line_index(lines, line_index)
    lines = list(lines)
    lines[index] = clamp_line_coordinate(lines, index, coordinate, min_gap=min_gap)
    if axis == "x":
        x_lines = lines
    else:
        y_lines = lines
    cells = _refresh_cell_geometry(get_cells(document), x_lines, y_lines)
    updated = set_axes_on_copy(document, x_lines, y_lines)
    updated = set_cells_on_copy(updated, cells)
    return _refresh_text(updated, recompute_text=recompute_text)


def add_line(
    document: Any,
    axis: str,
    coordinate: Number,
    *,
    min_gap: float = 2.0,
    recompute_text: bool = True,
) -> Any:
    """Add a grid line, splitting cells that cover the affected band."""

    axis = _axis(axis)
    x_lines = get_axis(document, "x")
    y_lines = get_axis(document, "y")
    lines = list(x_lines if axis == "x" else y_lines)
    coord = float(coordinate)
    if coord <= lines[0] + min_gap or coord >= lines[-1] - min_gap:
        raise CommandError(f"cannot add {axis}-line outside interior bounds: {coord}")

    insert_index = 1
    while insert_index < len(lines) and lines[insert_index] < coord:
        insert_index += 1
    prev_value = lines[insert_index - 1]
    next_value = lines[insert_index]
    if coord - prev_value < min_gap or next_value - coord < min_gap:
        raise CommandError(f"cannot add line at {coord}; violates min_gap={min_gap}")

    lines.insert(insert_index, coord)
    remapped_cells: list[Any] = []
    for cell in get_cells(document):
        remapped_cells.extend(_remap_cell_axis_for_add(cell, axis, insert_index))

    if axis == "x":
        x_lines = lines
    else:
        y_lines = lines
    cells = _refresh_cell_geometry(remapped_cells, x_lines, y_lines)
    updated = set_axes_on_copy(document, x_lines, y_lines)
    updated = set_cells_on_copy(updated, cells)
    return _refresh_text(updated, recompute_text=recompute_text)


def delete_line(
    document: Any,
    axis: str,
    line_index: int,
    *,
    recompute_text: bool = True,
) -> Any:
    """Delete an interior grid line and merge duplicate adjacent cells."""

    axis = _axis(axis)
    x_lines = get_axis(document, "x")
    y_lines = get_axis(document, "y")
    lines = list(x_lines if axis == "x" else y_lines)
    index = _line_index(lines, line_index)
    del lines[index]

    remapped = [_remap_cell_axis_for_delete(cell, axis, index) for cell in get_cells(document)]
    remapped = _merge_duplicate_cells(remapped)
    if axis == "x":
        x_lines = lines
    else:
        y_lines = lines
    cells = _refresh_cell_geometry(remapped, x_lines, y_lines)
    updated = set_axes_on_copy(document, x_lines, y_lines)
    updated = set_cells_on_copy(updated, cells)
    return _refresh_text(updated, recompute_text=recompute_text)


def _selection_bounds(selection: Iterable[Any]) -> tuple[int, int, int, int]:
    rows: list[int] = []
    cols: list[int] = []
    end_rows: list[int] = []
    end_cols: list[int] = []
    for item in selection:
        if isinstance(item, tuple) and len(item) == 2:
            row, col = int(item[0]), int(item[1])
            rows.append(row)
            cols.append(col)
            end_rows.append(row + 1)
            end_cols.append(col + 1)
        else:
            rows.append(int(_get(item, "row")))
            cols.append(int(_get(item, "col")))
            end_rows.append(int(_get(item, "end_row")))
            end_cols.append(int(_get(item, "end_col")))
    if not rows:
        raise CommandError("selection is empty")
    return min(rows), min(cols), max(end_rows), max(end_cols)


def _overlaps(cell: Any, row: int, col: int, end_row: int, end_col: int) -> bool:
    return not (
        int(_get(cell, "end_row")) <= row
        or int(_get(cell, "row")) >= end_row
        or int(_get(cell, "end_col")) <= col
        or int(_get(cell, "col")) >= end_col
    )


def _inside(cell: Any, row: int, col: int, end_row: int, end_col: int) -> bool:
    return (
        row <= int(_get(cell, "row"))
        and int(_get(cell, "end_row")) <= end_row
        and col <= int(_get(cell, "col"))
        and int(_get(cell, "end_col")) <= end_col
    )


def _validate_rectangular_cover(cells: Sequence[Any], row: int, col: int, end_row: int, end_col: int) -> None:
    covered: set[tuple[int, int]] = set()
    for cell in cells:
        for rr in range(int(_get(cell, "row")), int(_get(cell, "end_row"))):
            for cc in range(int(_get(cell, "col")), int(_get(cell, "end_col"))):
                if (rr, cc) in covered:
                    raise CommandError("selection contains overlapping cells")
                covered.add((rr, cc))
    expected = {(rr, cc) for rr in range(row, end_row) for cc in range(col, end_col)}
    # Real Docling records can be sparse: an intended merge rectangle may contain
    # implicit empty cells that are not present in the source cell list.  Reject
    # cells outside the requested rectangle, but allow missing slots.
    if not covered.issubset(expected):
        extra = sorted(covered - expected)[:5]
        raise CommandError(f"selection includes cells outside rectangle; extra={extra}")


def merge_cells(
    document: Any,
    selection: Iterable[Any],
    *,
    recompute_text: bool = True,
) -> Any:
    """Merge a rectangular cell selection into one spanned cell."""

    selection_items = list(selection)
    row, col, end_row, end_col = _selection_bounds(selection_items)
    cells = get_cells(document)
    affected = [cell for cell in cells if _overlaps(cell, row, col, end_row, end_col)]
    affected_keys = {
        (
            int(_get(cell, "row")),
            int(_get(cell, "col")),
            int(_get(cell, "end_row")),
            int(_get(cell, "end_col")),
        )
        for cell in affected
    }
    for item in selection_items:
        if isinstance(item, tuple) or not all(_has_field(item, field_name) for field_name in ("row", "col", "end_row", "end_col")):
            continue
        key = (
            int(_get(item, "row")),
            int(_get(item, "col")),
            int(_get(item, "end_row")),
            int(_get(item, "end_col")),
        )
        if key not in affected_keys and _inside(item, row, col, end_row, end_col):
            affected.append(item)
            affected_keys.add(key)
    if not affected:
        raise CommandError("selection does not match any cells")
    if any(not _inside(cell, row, col, end_row, end_col) for cell in affected):
        raise CommandError("selection cuts through an existing spanned cell")
    _validate_rectangular_cover(affected, row, col, end_row, end_col)

    unaffected = [cell for cell in cells if cell not in affected]
    ordered = _sort_cells(affected)
    base = ordered[0]
    source_span_ids: list[str] = []
    for cell in ordered:
        source_span_ids.extend(str(item) for item in (_get(cell, "source_span_ids", []) or []))
    span_like = []
    for cell in ordered:
        bbox = _get(cell, "bbox_px", None)
        text = str(_get(cell, "text", "") or "")
        if bbox and text:
            span_like.append({"id": _cell_id(cell), "text": text, "bbox_px": bbox})
    merged_text = reconstruct_cell_text(span_like) if span_like else " ".join(str(_get(cell, "text", "") or "").strip() for cell in ordered).strip()

    merged = _replace_record(
        base,
        row=row,
        col=col,
        end_row=end_row,
        end_col=end_col,
        row_span=end_row - row,
        col_span=end_col - col,
        text=merged_text,
        is_column_header=any(bool(_get(cell, "is_column_header", False)) for cell in affected),
        is_row_header=any(bool(_get(cell, "is_row_header", False)) for cell in affected),
        is_row_section=any(bool(_get(cell, "is_row_section", False)) for cell in affected),
        is_fillable=any(bool(_get(cell, "is_fillable", False)) for cell in affected),
        source_span_ids=list(dict.fromkeys(source_span_ids)),
    )
    x_lines = get_axis(document, "x")
    y_lines = get_axis(document, "y")
    merged = update_cell_geometry(merged, x_lines, y_lines)
    updated = set_cells_on_copy(document, _sort_cells([*unaffected, merged]))
    return _refresh_text(updated, recompute_text=recompute_text)


def _clone_cell_for_unit(cell: Any, row: int, col: int, *, text: str) -> Any:
    return _replace_record(
        cell,
        row=row,
        col=col,
        end_row=row + 1,
        end_col=col + 1,
        row_span=1,
        col_span=1,
        text=text,
        source_span_ids=[],
        manual_text_override=bool(text),
    )


def unmerge_cell(
    document: Any,
    target: Any,
    *,
    recompute_text: bool = True,
) -> Any:
    """Split one spanned cell into atomic cells."""

    cells = get_cells(document)
    if isinstance(target, int):
        try:
            cell = cells[target]
        except IndexError as exc:
            raise CommandError(f"cell index {target} out of range") from exc
    elif isinstance(target, tuple) and len(target) == 2:
        row, col = int(target[0]), int(target[1])
        matches = [cell for cell in cells if int(_get(cell, "row")) <= row < int(_get(cell, "end_row")) and int(_get(cell, "col")) <= col < int(_get(cell, "end_col"))]
        if not matches:
            raise CommandError(f"no cell contains {target}")
        cell = matches[0]
    else:
        cell = target

    row = int(_get(cell, "row"))
    col = int(_get(cell, "col"))
    end_row = int(_get(cell, "end_row"))
    end_col = int(_get(cell, "end_col"))
    if end_row - row == 1 and end_col - col == 1:
        raise CommandError("cell is already atomic")

    original_text = str(_get(cell, "text", "") or "")
    units: list[Any] = []
    first = True
    for rr in range(row, end_row):
        for cc in range(col, end_col):
            units.append(_clone_cell_for_unit(cell, rr, cc, text=original_text if first else ""))
            first = False

    remaining = [other for other in cells if other is not cell]
    x_lines = get_axis(document, "x")
    y_lines = get_axis(document, "y")
    units = _refresh_cell_geometry(units, x_lines, y_lines)
    updated = set_cells_on_copy(document, _sort_cells([*remaining, *units]))
    return _refresh_text(updated, recompute_text=recompute_text)



def _copy_state_into(target: Any, source: Any) -> None:
    if isinstance(target, MutableMapping) and isinstance(source, Mapping):
        target.clear()
        target.update(copy.deepcopy(dict(source)))
        return
    if is_dataclass(target) and is_dataclass(source):
        for item in fields(target):
            object.__setattr__(target, item.name, copy.deepcopy(getattr(source, item.name)))
        return
    if hasattr(target, "__dict__") and hasattr(source, "__dict__"):
        target.__dict__.clear()
        target.__dict__.update(copy.deepcopy(source.__dict__))
        return
    raise CommandError("cannot copy command state into target document")


@dataclass
class TableEditCommand:
    """Base class for undoable table edit commands."""

    document: Any = field(default=None, init=False, repr=False)
    before: Any = field(default=None, init=False, repr=False)
    after: Any = field(default=None, init=False, repr=False)

    def apply(self, document: Any | None = None) -> Any:
        target = document if document is not None else self.document
        if target is None:
            raise CommandError("command.apply() needs a document")
        mutate_in_place = document is None and self.document is not None
        self.before = clone_document(target)
        self.after = self._apply(clone_document(target))
        if mutate_in_place:
            _copy_state_into(target, self.after)
            return target
        return clone_document(self.after)

    def revert(self, document: Any | None = None) -> Any:
        if self.before is None:
            raise CommandError("command has not been applied")
        target = document if document is not None else self.document
        mutate_in_place = document is None and target is not None
        if mutate_in_place:
            _copy_state_into(target, self.before)
            return target
        return clone_document(self.before)

    def _apply(self, document: Any) -> Any:  # pragma: no cover - abstract hook
        raise NotImplementedError


class MoveLineCommand(TableEditCommand):
    def __init__(
        self,
        *args: Any,
        axis: str | None = None,
        line_index: int | None = None,
        edge_index: int | None = None,
        coordinate: float | None = None,
        min_gap: float = 2.0,
        recompute_text: bool = True,
    ) -> None:
        super().__init__()
        positional = list(args)
        if positional and not isinstance(positional[0], str):
            self.document = positional.pop(0)
        if positional and axis is None:
            axis = positional.pop(0)
        if positional and line_index is None and edge_index is None:
            line_index = int(positional.pop(0))
        if positional and coordinate is None:
            coordinate = float(positional.pop(0))
        self.axis = _axis(axis or "x")
        self.line_index = int(edge_index if edge_index is not None else line_index if line_index is not None else 1)
        if coordinate is None:
            raise CommandError("MoveLineCommand requires coordinate")
        self.coordinate = float(coordinate)
        self.min_gap = min_gap
        self.recompute_text = recompute_text

    def _apply(self, document: Any) -> Any:
        return move_line(document, self.axis, self.line_index, self.coordinate, min_gap=self.min_gap, recompute_text=self.recompute_text)


class AddLineCommand(TableEditCommand):
    def __init__(
        self,
        *args: Any,
        axis: str | None = None,
        coordinate: float | None = None,
        min_gap: float = 2.0,
        recompute_text: bool = True,
    ) -> None:
        super().__init__()
        positional = list(args)
        if positional and not isinstance(positional[0], str):
            self.document = positional.pop(0)
        if positional and axis is None:
            axis = positional.pop(0)
        if positional and coordinate is None:
            coordinate = float(positional.pop(0))
        self.axis = _axis(axis or "x")
        if coordinate is None:
            raise CommandError("AddLineCommand requires coordinate")
        self.coordinate = float(coordinate)
        self.min_gap = min_gap
        self.recompute_text = recompute_text

    def _apply(self, document: Any) -> Any:
        return add_line(document, self.axis, self.coordinate, min_gap=self.min_gap, recompute_text=self.recompute_text)


class DeleteLineCommand(TableEditCommand):
    def __init__(
        self,
        *args: Any,
        axis: str | None = None,
        line_index: int | None = None,
        edge_index: int | None = None,
        recompute_text: bool = True,
    ) -> None:
        super().__init__()
        positional = list(args)
        if positional and not isinstance(positional[0], str):
            self.document = positional.pop(0)
        if positional and axis is None:
            axis = positional.pop(0)
        if positional and line_index is None and edge_index is None:
            line_index = int(positional.pop(0))
        self.axis = _axis(axis or "x")
        self.line_index = int(edge_index if edge_index is not None else line_index if line_index is not None else 1)
        self.recompute_text = recompute_text

    def _apply(self, document: Any) -> Any:
        return delete_line(document, self.axis, self.line_index, recompute_text=self.recompute_text)


class MergeCellsCommand(TableEditCommand):
    def __init__(
        self,
        *args: Any,
        selection: Sequence[Any] | None = None,
        row0: int | None = None,
        row1: int | None = None,
        col0: int | None = None,
        col1: int | None = None,
        recompute_text: bool = True,
    ) -> None:
        super().__init__()
        positional = list(args)
        if positional and selection is None and not isinstance(positional[0], (list, tuple)):
            self.document = positional.pop(0)
        if positional and selection is None:
            selection = positional.pop(0)
        if selection is None:
            if None in (row0, row1, col0, col1):
                raise CommandError("MergeCellsCommand requires selection or row0/row1/col0/col1")
            assert row0 is not None and row1 is not None and col0 is not None and col1 is not None
            selection = [(rr, cc) for rr in range(int(row0), int(row1)) for cc in range(int(col0), int(col1))]
        self.selection = list(selection)
        self.recompute_text = recompute_text

    def _apply(self, document: Any) -> Any:
        return merge_cells(document, self.selection, recompute_text=self.recompute_text)


class UnmergeCellCommand(TableEditCommand):
    def __init__(
        self,
        *args: Any,
        target: Any = None,
        cell_index: int | None = None,
        recompute_text: bool = True,
    ) -> None:
        super().__init__()
        positional = list(args)
        if positional and target is None and cell_index is None and not isinstance(positional[0], (int, tuple)):
            self.document = positional.pop(0)
        if positional and target is None and cell_index is None:
            target = positional.pop(0)
        self.target = cell_index if cell_index is not None else target
        if self.target is None:
            raise CommandError("UnmergeCellCommand requires target or cell_index")
        self.recompute_text = recompute_text

    def _apply(self, document: Any) -> Any:
        return unmerge_cell(document, self.target, recompute_text=self.recompute_text)


class AssignTextCommand(TableEditCommand):
    def __init__(self, document: Any | None = None, *, spans: Sequence[Any] | None = None) -> None:
        super().__init__()
        self.document = document
        self.spans = spans

    def _apply(self, document: Any) -> Any:
        return assign_text_to_document(document, spans=self.spans)


class EditCellTextCommand(TableEditCommand):
    def __init__(self, *args: Any, target: int | tuple[int, int] | None = None, text: str | None = None) -> None:
        super().__init__()
        positional = list(args)
        if positional and target is None and not isinstance(positional[0], (int, tuple)):
            self.document = positional.pop(0)
        if positional and target is None:
            target = positional.pop(0)
        if positional and text is None:
            text = str(positional.pop(0))
        if target is None or text is None:
            raise CommandError("EditCellTextCommand requires target and text")
        self.target = target
        self.text = text

    def _apply(self, document: Any) -> Any:
        cells = get_cells(document)
        updated_cells: list[Any] = []
        changed = False
        for index, cell in enumerate(cells):
            match = False
            if isinstance(self.target, int):
                match = index == self.target
            else:
                row, col = int(self.target[0]), int(self.target[1])
                match = int(_get(cell, "row")) <= row < int(_get(cell, "end_row")) and int(_get(cell, "col")) <= col < int(_get(cell, "end_col"))
            if match:
                updated_cells.append(_replace_record(cell, text=self.text, manual_text_override=True))
                changed = True
            else:
                updated_cells.append(cell)
        if not changed:
            raise CommandError(f"cell target {self.target!r} not found")
        return set_cells_on_copy(document, updated_cells)


class CommandStack:
    """Small undo/redo stack suitable for the PySide UI layer."""

    def __init__(self, document: Any | None = None):
        self.document = document
        self._undo: list[TableEditCommand] = []
        self._redo: list[TableEditCommand] = []

    def do(self, command: TableEditCommand) -> Any:
        if self.document is None:
            self.document = command.apply()
        else:
            self.document = command.apply(self.document)
        self._undo.append(command)
        self._redo.clear()
        return self.document

    def undo(self) -> Any:
        if not self._undo:
            raise CommandError("nothing to undo")
        command = self._undo.pop()
        if self.document is None:
            self.document = command.revert()
        else:
            self.document = command.revert(self.document)
            if command.document is not None:
                _copy_state_into(command.document, self.document)
        self._redo.append(command)
        return self.document

    def redo(self) -> Any:
        if not self._redo:
            raise CommandError("nothing to redo")
        command = self._redo.pop()
        if command.after is None:
            self.document = command.apply(self.document)
        else:
            self.document = clone_document(command.after)
            if command.document is not None:
                _copy_state_into(command.document, self.document)
        self._undo.append(command)
        return self.document

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)
