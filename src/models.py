"""Core data model for the table ground-truth editor.

The model intentionally stays GUI-free and dependency-free so the PySide scene,
undoable commands, validation scripts, and tests can share the same primitives.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import Any, Iterable, Literal, Mapping, Sequence

AxisName = Literal["horizontal", "vertical"]

EPSILON = 1e-6
DEFAULT_MIN_LINE_GAP = 2.0


def _finite_float(value: Any, *, field_name: str = "value") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a finite number, got {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return result


def _coerce_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an int, got {value!r}")
    return value


def _line_key(value: Any) -> int:
    if isinstance(value, bool):
        raise TypeError(f"line key must be an integer-like string, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    raise TypeError(f"line key must be an integer-like string, got {value!r}")


def _is_monotonic(values: Sequence[float], *, epsilon: float = EPSILON) -> bool:
    """Return True when values are strictly increasing within a small tolerance."""
    return all(float(values[i]) < float(values[i + 1]) - epsilon for i in range(len(values) - 1))


def _dedupe_sorted(values: Iterable[float], *, min_gap: float = 0.0) -> tuple[float, ...]:
    gap = max(0.0, float(min_gap))
    result: list[float] = []
    for value in sorted(values):
        v = _finite_float(value)
        if result and abs(v - result[-1]) <= max(gap, EPSILON):
            continue
        result.append(v)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class Rect:
    """A finite left/top/right/bottom rectangle in image coordinates."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        left = _finite_float(self.left, field_name="left")
        top = _finite_float(self.top, field_name="top")
        right = _finite_float(self.right, field_name="right")
        bottom = _finite_float(self.bottom, field_name="bottom")
        if right < left:
            raise ValueError(f"right must be >= left: {right} < {left}")
        if bottom < top:
            raise ValueError(f"bottom must be >= top: {bottom} < {top}")
        object.__setattr__(self, "left", left)
        object.__setattr__(self, "top", top)
        object.__setattr__(self, "right", right)
        object.__setattr__(self, "bottom", bottom)

    @classmethod
    def from_sequence(cls, values: Sequence[Any]) -> "Rect":
        if len(values) != 4:
            raise ValueError(f"bbox must have 4 values, got {len(values)}")
        return cls(values[0], values[1], values[2], values[3])

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)

    def contains_point(self, x: float, y: float, *, epsilon: float = EPSILON) -> bool:
        return (
            self.left - epsilon <= x <= self.right + epsilon
            and self.top - epsilon <= y <= self.bottom + epsilon
        )

    def intersection_area(self, other: "Rect") -> float:
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return 0.0
        return (right - left) * (bottom - top)

    def crosses_vertical(self, x: float, *, epsilon: float = EPSILON) -> bool:
        x = _finite_float(x, field_name="x")
        return self.left + epsilon < x < self.right - epsilon

    def crosses_horizontal(self, y: float, *, epsilon: float = EPSILON) -> bool:
        y = _finite_float(y, field_name="y")
        return self.top + epsilon < y < self.bottom - epsilon

    def to_list(self) -> list[float]:
        return [self.left, self.top, self.right, self.bottom]


@dataclass(frozen=True, slots=True)
class GridAxis:
    """Editable interior grid lines for one axis.

    Docling JSON stores interior line dictionaries (`h_lines` / `v_lines`) and a
    declared row/column count. Real ground-truth records can have fewer physical
    dividers than `declared_count - 1`, so this model preserves the declared
    count while interpolating missing edge coordinates only for drawing/exported
    cell bboxes.

    Line dictionary keys are treated as the zero-based boundary after a band:
    key `0` is edge index `1`, key `1` is edge index `2`, and so on.
    """

    name: AxisName
    size: int
    declared_count: int
    lines_by_index: Mapping[int, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.name not in ("horizontal", "vertical"):
            raise ValueError(f"unsupported axis name: {self.name!r}")
        size = _coerce_int(self.size, field_name="size")
        declared = _coerce_int(self.declared_count, field_name="declared_count")
        if size <= 0:
            raise ValueError(f"axis size must be > 0, got {size}")
        if declared < 0:
            raise ValueError(f"declared_count must be >= 0, got {declared}")
        max_key = max(0, declared - 2)
        clean: dict[int, float] = {}
        for raw_key, raw_value in dict(self.lines_by_index).items():
            key = _line_key(raw_key)
            value = _finite_float(raw_value, field_name=f"{self.name} line {key}")
            if key < 0:
                continue
            if declared > 0 and key > max_key:
                # Ignore impossible extra line keys instead of corrupting the
                # declared grid. The validator also rejects too many lines.
                continue
            if 0.0 < value < float(size):
                clean[key] = value
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "declared_count", declared)
        object.__setattr__(self, "lines_by_index", clean)

    @classmethod
    def from_line_dict(
        cls,
        *,
        name: AxisName,
        size: int,
        declared_count: int,
        lines: Mapping[Any, Any] | None,
        min_gap: float = 0.0,
    ) -> "GridAxis":
        raw = lines or {}
        keyed = {_line_key(k): _finite_float(v, field_name=f"{name} line {k}") for k, v in raw.items()}
        if min_gap > 0 and keyed:
            filtered: dict[int, float] = {}
            previous: float | None = None
            for key, value in sorted(keyed.items(), key=lambda item: (item[1], item[0])):
                if previous is not None and value - previous < min_gap:
                    continue
                filtered[key] = value
                previous = value
            keyed = filtered
        return cls(name=name, size=size, declared_count=declared_count, lines_by_index=keyed)

    @classmethod
    def from_interior_values(
        cls,
        *,
        name: AxisName,
        size: int,
        interior_values: Iterable[float],
        min_gap: float = DEFAULT_MIN_LINE_GAP,
    ) -> "GridAxis":
        values = [v for v in _dedupe_sorted(interior_values, min_gap=min_gap) if 0.0 < v < size]
        return cls(
            name=name,
            size=size,
            declared_count=len(values) + 1 if values else 1,
            lines_by_index={idx: value for idx, value in enumerate(values)},
        )

    @property
    def line_keys(self) -> tuple[int, ...]:
        return tuple(sorted(self.lines_by_index))

    @property
    def explicit_values(self) -> tuple[float, ...]:
        return tuple(self.lines_by_index[key] for key in self.line_keys)

    @property
    def sorted_explicit_values(self) -> tuple[float, ...]:
        return tuple(sorted(self.lines_by_index.values()))

    @property
    def full_edges(self) -> tuple[float, ...]:
        """Return `declared_count + 1` monotonic edge coordinates.

        The preferred reconstruction uses Docling line keys as boundary indexes.
        Some real GT files contain a small number of non-monotonic keyed values;
        in that case, fall back to sorted physical divider coordinates for
        drawing/exported cell geometry while leaving the original line dict
        untouched for round-trip export.
        """

        if self.declared_count <= 0:
            return (0.0, float(self.size))
        keyed_edges = self._keyed_full_edges()
        if _is_monotonic(keyed_edges):
            return keyed_edges
        return self._sequential_full_edges()

    def _keyed_full_edges(self) -> tuple[float, ...]:
        edges: list[float | None] = [None] * (self.declared_count + 1)
        edges[0] = 0.0
        edges[-1] = float(self.size)
        for key, value in self.lines_by_index.items():
            edge_index = key + 1
            if 0 < edge_index < len(edges):
                edges[edge_index] = value

        known = [idx for idx, value in enumerate(edges) if value is not None]
        for left_idx, right_idx in zip(known, known[1:]):
            left_value = float(edges[left_idx])  # type: ignore[arg-type]
            right_value = float(edges[right_idx])  # type: ignore[arg-type]
            span = right_idx - left_idx
            if span <= 1:
                continue
            step = (right_value - left_value) / span
            for offset in range(1, span):
                edges[left_idx + offset] = left_value + step * offset

        # Defensive fallback for pathological inputs.
        for idx, value in enumerate(edges):
            if value is None:
                edges[idx] = float(self.size) * idx / max(1, self.declared_count)
        return tuple(float(v) for v in edges)

    def _sequential_full_edges(self) -> tuple[float, ...]:
        explicit = list(self.sorted_explicit_values)
        needed = max(0, self.declared_count - 1)
        if len(explicit) > needed:
            explicit = explicit[:needed]
        known: list[float | None] = [0.0] + explicit + [float(self.size)]
        missing = (self.declared_count + 1) - len(known)
        if missing > 0:
            # Add missing dividers at the end of the final physical gap. This is
            # a geometry-only fallback for sparse/malformed axes; the original
            # sparse line dict remains available in `to_line_dict()`.
            left_idx = len(known) - 2
            left = float(known[left_idx] if known[left_idx] is not None else 0.0)
            right = float(self.size)
            step = (right - left) / (missing + 1)
            inserted = [left + step * (i + 1) for i in range(missing)]
            known = known[:-1] + inserted + [float(self.size)]
        return tuple(float(v) for v in known)

    def edge_at(self, index: int) -> float:
        if not 0 <= index <= self.declared_count:
            raise IndexError(f"edge index {index} outside 0..{self.declared_count}")
        return self.full_edges[index]

    def cell_span(self, start: int, end: int) -> tuple[float, float]:
        if start < 0 or end <= start or end > self.declared_count:
            raise ValueError(
                f"cell span {start}..{end} outside declared {self.declared_count} {self.name} bands"
            )
        return self.edge_at(start), self.edge_at(end)

    def to_line_dict(self) -> dict[str, float]:
        """Return an interior-line dict sorted by integer key for Docling JSON."""

        return {str(key): float(self.lines_by_index[key]) for key in self.line_keys}

    def with_moved_line(
        self,
        key: int,
        coordinate: float,
        *,
        min_gap: float = DEFAULT_MIN_LINE_GAP,
    ) -> "GridAxis":
        key = _line_key(key)
        if key not in self.lines_by_index:
            raise KeyError(f"no {self.name} line with key {key}")
        coordinate = self._checked_coordinate(coordinate, key=key, min_gap=min_gap)
        updated = dict(self.lines_by_index)
        updated[key] = coordinate
        return replace(self, lines_by_index=updated)

    def with_inserted_line(
        self,
        coordinate: float,
        *,
        min_gap: float = DEFAULT_MIN_LINE_GAP,
    ) -> "GridAxis":
        coordinate = self._checked_coordinate(coordinate, key=None, min_gap=min_gap)
        values = list(self.sorted_explicit_values) + [coordinate]
        values = list(_dedupe_sorted(values, min_gap=min_gap))
        return GridAxis.from_interior_values(
            name=self.name,
            size=self.size,
            interior_values=values,
            min_gap=min_gap,
        )

    def with_deleted_line(self, key: int) -> "GridAxis":
        key = _line_key(key)
        if key not in self.lines_by_index:
            raise KeyError(f"no {self.name} line with key {key}")
        values = [value for old_key, value in self.lines_by_index.items() if old_key != key]
        return GridAxis.from_interior_values(
            name=self.name,
            size=self.size,
            interior_values=values,
            min_gap=0.0,
        )

    def _checked_coordinate(self, coordinate: float, *, key: int | None, min_gap: float) -> float:
        value = _finite_float(coordinate, field_name=f"{self.name} coordinate")
        if not 0.0 < value < float(self.size):
            raise ValueError(f"{self.name} coordinate {value} outside 0..{self.size}")
        gap = max(0.0, min_gap)
        for old_key, old_value in self.lines_by_index.items():
            if key is not None and old_key == key:
                continue
            if abs(value - old_value) < gap:
                raise ValueError(
                    f"{self.name} coordinate {value} violates min gap {gap} near line {old_key}"
                )
        return value


@dataclass(frozen=True, slots=True)
class TextSpan:
    """Original JSON text + bbox preserved for assignment/audit."""

    span_id: str
    text: str
    bbox: Rect
    source_cell_index: int | None = None
    assigned_cell_key: tuple[int, int, int, int] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def reading_order_key(self) -> tuple[float, float, str]:
        return (self.bbox.top, self.bbox.left, self.span_id)

    @property
    def index(self) -> str:
        """Compatibility alias used by text-assignment tests/helpers."""

        return self.span_id

    def to_state(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "text": self.text,
            "bbox": self.bbox.to_list(),
            "source_cell_index": self.source_cell_index,
            "assigned_cell_key": list(self.assigned_cell_key) if self.assigned_cell_key else None,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class TableCell:
    """One logical table cell, possibly spanning rows/columns."""

    row: int
    col: int
    end_row: int
    end_col: int
    text: str = ""
    is_column_header: bool = False
    is_row_header: bool = False
    is_row_section: bool = False
    is_fillable: bool = False
    source_bbox: Rect | None = None
    assigned_span_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        row = _coerce_int(self.row, field_name="row")
        col = _coerce_int(self.col, field_name="col")
        end_row = _coerce_int(self.end_row, field_name="end_row")
        end_col = _coerce_int(self.end_col, field_name="end_col")
        if row < 0 or col < 0 or end_row <= row or end_col <= col:
            raise ValueError(f"invalid cell span: row={row} col={col} end_row={end_row} end_col={end_col}")
        object.__setattr__(self, "row", row)
        object.__setattr__(self, "col", col)
        object.__setattr__(self, "end_row", end_row)
        object.__setattr__(self, "end_col", end_col)
        object.__setattr__(self, "text", str(self.text))
        for attr in ("is_column_header", "is_row_header", "is_row_section", "is_fillable"):
            if not isinstance(getattr(self, attr), bool):
                raise TypeError(f"{attr} must be bool")

    @property
    def row_span(self) -> int:
        return self.end_row - self.row

    @property
    def col_span(self) -> int:
        return self.end_col - self.col

    @property
    def key(self) -> tuple[int, int, int, int]:
        return (self.row, self.col, self.end_row, self.end_col)

    def grid_bbox(self, row_axis: GridAxis, col_axis: GridAxis) -> Rect:
        top, bottom = row_axis.cell_span(self.row, self.end_row)
        left, right = col_axis.cell_span(self.col, self.end_col)
        return Rect(left, top, right, bottom)

    def to_docling(self, *, bbox: Rect) -> dict[str, Any]:
        return {
            "row": self.row,
            "col": self.col,
            "end_row": self.end_row,
            "end_col": self.end_col,
            "row_span": self.row_span,
            "col_span": self.col_span,
            "text": self.text,
            "is_column_header": self.is_column_header,
            "is_row_header": self.is_row_header,
            "is_row_section": self.is_row_section,
            "is_fillable": self.is_fillable,
            "bbox_px": bbox.to_list(),
        }

    def to_state(self, *, bbox: Rect | None = None) -> dict[str, Any]:
        return {
            "row": self.row,
            "col": self.col,
            "end_row": self.end_row,
            "end_col": self.end_col,
            "row_span": self.row_span,
            "col_span": self.col_span,
            "text": self.text,
            "is_column_header": self.is_column_header,
            "is_row_header": self.is_row_header,
            "is_row_section": self.is_row_section,
            "is_fillable": self.is_fillable,
            "bbox_px": bbox.to_list() if bbox else None,
            "source_bbox": self.source_bbox.to_list() if self.source_bbox else None,
            "assigned_span_ids": list(self.assigned_span_ids),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class GridWarning:
    """Warning that a text bbox crosses an interior grid line."""

    warning_id: str
    axis: AxisName
    line_key: int
    line_coordinate: float
    span_id: str
    source_cell_index: int | None
    candidate: Literal["left_right_split", "top_bottom_split"]
    bbox: Rect
    message: str

    @property
    def kind(self) -> str:
        if self.axis == "vertical":
            return "text_crosses_vertical_line"
        return "text_crosses_horizontal_line"

    def to_state(self) -> dict[str, Any]:
        return {
            "warning_id": self.warning_id,
            "axis": self.axis,
            "line_key": self.line_key,
            "line_coordinate": self.line_coordinate,
            "span_id": self.span_id,
            "source_cell_index": self.source_cell_index,
            "candidate": self.candidate,
            "bbox": self.bbox.to_list(),
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class TableDocument:
    """Loaded editor document for one image/json table pair."""

    stem: str
    source_pdf: str
    page_no: int
    table_index: int
    global_index: int
    image_size: tuple[int, int]
    table_bbox_px: Rect
    row_axis: GridAxis
    col_axis: GridAxis
    cells: tuple[TableCell, ...]
    text_spans: tuple[TextSpan, ...]
    warnings: tuple[GridWarning, ...] = ()
    layout_tedss_score: float | None = None
    image_path: str | None = None
    json_path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.image_size) != 2:
            raise ValueError(f"image_size must be (width, height), got {self.image_size!r}")
        width = _coerce_int(self.image_size[0], field_name="image_width")
        height = _coerce_int(self.image_size[1], field_name="image_height")
        if width <= 0 or height <= 0:
            raise ValueError(f"image_size values must be > 0, got {self.image_size!r}")
        if self.col_axis.size != width:
            raise ValueError(f"vertical axis size {self.col_axis.size} != image width {width}")
        if self.row_axis.size != height:
            raise ValueError(f"horizontal axis size {self.row_axis.size} != image height {height}")
        object.__setattr__(self, "image_size", (width, height))

    @property
    def num_rows(self) -> int:
        return self.row_axis.declared_count

    @property
    def num_cols(self) -> int:
        return self.col_axis.declared_count

    @property
    def width(self) -> int:
        return self.image_size[0]

    @property
    def height(self) -> int:
        return self.image_size[1]

    @property
    def x_edges(self) -> list[float]:
        return list(self.col_axis.full_edges)

    @property
    def y_edges(self) -> list[float]:
        return list(self.row_axis.full_edges)

    def cell_bbox(self, cell: TableCell) -> Rect:
        return cell.grid_bbox(self.row_axis, self.col_axis)

    def with_warnings(self, warnings: Iterable[GridWarning]) -> "TableDocument":
        return replace(self, warnings=tuple(warnings))

    def rebuild_warnings(self) -> "TableDocument":
        return self.with_warnings(detect_crossing_warnings(self.text_spans, self.row_axis, self.col_axis))

    def to_state_cells(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for cell in self.cells:
            try:
                bbox = self.cell_bbox(cell)
            except ValueError:
                bbox = cell.source_bbox
            result.append(cell.to_state(bbox=bbox))
        return result


def detect_crossing_warnings(
    text_spans: Iterable[TextSpan],
    row_axis: GridAxis,
    col_axis: GridAxis,
    *,
    epsilon: float = EPSILON,
) -> tuple[GridWarning, ...]:
    """Flag spans whose original bbox crosses an explicit interior grid line."""

    warnings: list[GridWarning] = []
    for span in text_spans:
        if not span.text.strip():
            continue
        for key, x in sorted(col_axis.lines_by_index.items(), key=lambda item: item[0]):
            if span.bbox.crosses_vertical(x, epsilon=epsilon):
                warning_id = f"{span.span_id}:v:{key}"
                warnings.append(
                    GridWarning(
                        warning_id=warning_id,
                        axis="vertical",
                        line_key=key,
                        line_coordinate=float(x),
                        span_id=span.span_id,
                        source_cell_index=span.source_cell_index,
                        candidate="left_right_split",
                        bbox=span.bbox,
                        message=(
                            f"text span {span.span_id} crosses vertical line {key} at x={x:.2f}; "
                            "candidate left/right split"
                        ),
                    )
                )
        for key, y in sorted(row_axis.lines_by_index.items(), key=lambda item: item[0]):
            if span.bbox.crosses_horizontal(y, epsilon=epsilon):
                warning_id = f"{span.span_id}:h:{key}"
                warnings.append(
                    GridWarning(
                        warning_id=warning_id,
                        axis="horizontal",
                        line_key=key,
                        line_coordinate=float(y),
                        span_id=span.span_id,
                        source_cell_index=span.source_cell_index,
                        candidate="top_bottom_split",
                        bbox=span.bbox,
                        message=(
                            f"text span {span.span_id} crosses horizontal line {key} at y={y:.2f}; "
                            "candidate top/bottom split"
                        ),
                    )
                )
    return tuple(warnings)
