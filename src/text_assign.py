"""Text assignment and warning helpers for the table GT editor.

This module intentionally depends only on the Python standard library.  It works
with the model dataclasses planned for :mod:`models`, but also accepts
plain dictionaries shaped like the existing Docling-compatible JSON records.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass, replace
import copy
import math
import re
from typing import Any, Iterable, Mapping, MutableMapping, Sequence, cast


Number = int | float
BBox = Sequence[Number]


@dataclass(frozen=True)
class Rect:
    """Crop-relative rectangle using left/top/right/bottom coordinates."""

    left: float
    top: float
    right: float
    bottom: float

    @classmethod
    def from_bbox(cls, bbox: BBox) -> "Rect":
        if len(bbox) != 4:
            raise ValueError(f"bbox must contain 4 coordinates, got {bbox!r}")
        left, top, right, bottom = (float(v) for v in bbox)
        if right < left:
            left, right = right, left
        if bottom < top:
            top, bottom = bottom, top
        return cls(left, top, right, bottom)

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)

    def to_bbox(self) -> list[float]:
        return [self.left, self.top, self.right, self.bottom]

    def contains_point(self, x: float, y: float, tolerance: float = 0.0) -> bool:
        return (
            self.left - tolerance <= x <= self.right + tolerance
            and self.top - tolerance <= y <= self.bottom + tolerance
        )

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.right <= other.left
            or self.left >= other.right
            or self.bottom <= other.top
            or self.top >= other.bottom
        )

    def intersection_area(self, other: "Rect") -> float:
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return 0.0
        return (right - left) * (bottom - top)

    def distance_to_point(self, x: float, y: float) -> float:
        dx = max(self.left - x, 0.0, x - self.right)
        dy = max(self.top - y, 0.0, y - self.bottom)
        return math.hypot(dx, dy)


@dataclass(frozen=True)
class TextSpan:
    """Editor-local text span.

    Existing val JSON only has cell-level text+bbox, so those cells are converted
    to TextSpan instances on load.  Future token/word sidecars can use the same
    object with a finer-grained source.
    """

    id: str
    text: str
    bbox_px: list[float]
    source: str = "json_cell"
    assigned_cell_id: str | None = None
    confidence: float = 0.0
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class Assignment:
    """Result of assigning one span to one cell."""

    span_id: str
    cell_id: str | None
    confidence: float
    method: str
    score_gap: float | None = None


@dataclass(frozen=True)
class TextAssignmentWarning:
    """Non-fatal issue surfaced to the GUI/audit layer."""

    kind: str
    message: str
    span_id: str | None = None
    cell_id: str | None = None
    axis: str | None = None
    line_index: int | None = None
    line_coord: float | None = None
    severity: str = "warning"
    details: dict[str, Any] | None = None


def _is_mapping(obj: Any) -> bool:
    return isinstance(obj, MutableMapping) or isinstance(obj, Mapping)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if _is_mapping(obj):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _set(obj: Any, name: str, value: Any) -> None:
    if isinstance(obj, MutableMapping):
        obj[name] = value
    else:
        setattr(obj, name, value)


def _replace_record(obj: Any, **updates: Any) -> Any:
    """Return a copy of *obj* with selected fields changed.

    Supports dictionaries, dataclasses, and simple mutable objects.  Unknown
    dataclass fields are ignored so this helper can interoperate with narrower
    model classes from another worker.
    """

    if isinstance(obj, MutableMapping):
        new = dict(obj)
        new.update(updates)
        return new
    if is_dataclass(obj):
        field_names = {f.name for f in fields(obj)}
        safe_updates = {k: v for k, v in updates.items() if k in field_names}
        try:
            return replace(cast(Any, obj), **safe_updates)
        except Exception:
            new = copy.copy(cast(Any, obj))
            for key, value in safe_updates.items():
                object.__setattr__(new, key, value)
            return new
    new = copy.copy(obj)
    for key, value in updates.items():
        if hasattr(new, key):
            setattr(new, key, value)
    return new


def _cell_id(cell: Any) -> str:
    explicit = _get(cell, "id", None) or _get(cell, "cell_id", None)
    if explicit is not None:
        return str(explicit)
    return f"r{int(_get(cell, 'row', 0))}c{int(_get(cell, 'col', 0))}:{int(_get(cell, 'end_row', 0))}x{int(_get(cell, 'end_col', 0))}"


def _span_id(span: Any) -> str:
    explicit = _get(span, "id", None) or _get(span, "span_id", None)
    if explicit is not None:
        return str(explicit)
    bbox = _span_bbox(span)
    text = _get(span, "text", "")
    return f"span:{hash((tuple(float(x) for x in bbox), text))}"


def _span_text(span: Any) -> str:
    return str(_get(span, "text", "") or "")


def _rect_to_bbox(rect: Any) -> list[float]:
    if hasattr(rect, "to_list"):
        return [float(v) for v in rect.to_list()]
    if all(hasattr(rect, name) for name in ("left", "top", "right", "bottom")):
        return [float(rect.left), float(rect.top), float(rect.right), float(rect.bottom)]
    if isinstance(rect, Sequence) and len(rect) == 4:
        return [float(v) for v in rect]
    return [0.0, 0.0, 0.0, 0.0]


def _span_bbox(span: Any) -> list[float]:
    bbox = _get(span, "bbox_px", None)
    if bbox is not None:
        return _rect_to_bbox(bbox)
    bbox = _get(span, "bbox", None)
    if bbox is not None:
        return _rect_to_bbox(bbox)
    return [0.0, 0.0, 0.0, 0.0]


def _span_source(span: Any) -> str:
    return str(_get(span, "source", "json_cell") or "json_cell")


def _replace_span(span: Any, **updates: Any) -> Any:
    if isinstance(span, TextSpan):
        return replace(span, **updates)
    if is_dataclass(span):
        field_names = {f.name for f in fields(span)}
        translated = dict(updates)
        if "assigned_cell_id" in translated and "assigned_cell_key" in field_names:
            translated["assigned_cell_key"] = _cell_key_from_id(translated.pop("assigned_cell_id"))
        safe_updates = {k: v for k, v in translated.items() if k in field_names}
        return replace(cast(Any, span), **safe_updates)
    return _replace_record(span, **updates)


def _axis_values(lines: Sequence[Number] | Mapping[str, Number]) -> list[float]:
    if isinstance(lines, Mapping):
        return [float(lines[k]) for k in sorted(lines, key=lambda x: int(x))]
    return [float(v) for v in lines]


def sorted_unique(values: Iterable[Number], *, min_gap: float = 0.0) -> list[float]:
    """Return sorted finite values, dropping near-duplicates inside *min_gap*."""

    result: list[float] = []
    for value in sorted(float(v) for v in values if math.isfinite(float(v))):
        if not result or abs(value - result[-1]) >= min_gap:
            result.append(value)
    return result


def cell_rect(
    cell: Any,
    x_lines: Sequence[Number] | Mapping[str, Number],
    y_lines: Sequence[Number] | Mapping[str, Number],
) -> Rect:
    """Return the current grid rectangle for *cell*.

    Some source Docling records contain fewer explicit line coordinates than
    their row/column counts.  When a cell index cannot be resolved against the
    supplied axes, fall back to the cell's existing bbox so assignment can still
    run on real validation records without inventing geometry.
    """

    xs = _axis_values(x_lines)
    ys = _axis_values(y_lines)
    row = int(_get(cell, "row"))
    col = int(_get(cell, "col"))
    end_row = int(_get(cell, "end_row"))
    end_col = int(_get(cell, "end_col"))
    try:
        return Rect(float(xs[col]), float(ys[row]), float(xs[end_col]), float(ys[end_row]))
    except IndexError as exc:
        bbox = _get(cell, "bbox_px", None)
        if isinstance(bbox, Sequence) and len(bbox) == 4:
            return Rect.from_bbox(bbox)
        raise ValueError(
            f"cell {cell!r} is outside grid {len(ys)-1}x{len(xs)-1}"
        ) from exc


def make_cell_bbox(
    cell: Any,
    x_lines: Sequence[Number] | Mapping[str, Number],
    y_lines: Sequence[Number] | Mapping[str, Number],
) -> list[float]:
    return cell_rect(cell, x_lines, y_lines).to_bbox()


def update_cell_geometry(
    cell: Any,
    x_lines: Sequence[Number] | Mapping[str, Number],
    y_lines: Sequence[Number] | Mapping[str, Number],
) -> Any:
    """Return *cell* with span fields and bbox updated from current axes."""

    row = int(_get(cell, "row"))
    col = int(_get(cell, "col"))
    end_row = int(_get(cell, "end_row"))
    end_col = int(_get(cell, "end_col"))
    return _replace_record(
        cell,
        row_span=end_row - row,
        col_span=end_col - col,
        bbox_px=make_cell_bbox(cell, x_lines, y_lines),
    )


def extract_text_spans_from_cells(cells: Sequence[Any], *, source: str = "json_cell") -> list[TextSpan]:
    """Create TextSpan objects from existing cell text and bbox fields."""

    spans: list[TextSpan] = []
    for index, cell in enumerate(cells):
        text = str(_get(cell, "text", "") or "")
        bbox = _get(cell, "bbox_px", None)
        if not text or not (isinstance(bbox, Sequence) and len(bbox) == 4):
            continue
        spans.append(
            TextSpan(
                id=f"{source}:{index}:{_cell_id(cell)}",
                text=text,
                bbox_px=[float(v) for v in bbox],
                source=source,
                assigned_cell_id=_cell_id(cell),
                confidence=1.0,
                metadata={
                    "row": _get(cell, "row", None),
                    "col": _get(cell, "col", None),
                    "end_row": _get(cell, "end_row", None),
                    "end_col": _get(cell, "end_col", None),
                },
            )
        )
    return spans


def _crossing_tolerance(rect: Rect) -> float:
    return max(2.0, 0.02 * max(rect.width, rect.height, 1.0))


def detect_crossing_warnings(
    spans: Sequence[Any],
    x_lines: Sequence[Number] | Mapping[str, Number],
    y_lines: Sequence[Number] | Mapping[str, Number],
    *,
    tolerance: float | None = None,
) -> list[TextAssignmentWarning]:
    """Detect interior grid lines that pass through text span bboxes."""

    xs = _axis_values(x_lines)
    ys = _axis_values(y_lines)
    warnings: list[TextAssignmentWarning] = []
    for span in spans:
        rect = Rect.from_bbox(_span_bbox(span))
        tol = _crossing_tolerance(rect) if tolerance is None else float(tolerance)
        sid = _span_id(span)
        for index, x in enumerate(xs[1:-1], start=1):
            if rect.left + tol < x < rect.right - tol:
                warnings.append(
                    TextAssignmentWarning(
                        kind="text_crosses_vertical_line",
                        message=f"Text span {sid} crosses vertical grid line {index} at x={x:.2f}",
                        span_id=sid,
                        axis="x",
                        line_index=index,
                        line_coord=x,
                        details={"bbox_px": rect.to_bbox(), "text": _span_text(span)},
                    )
                )
        for index, y in enumerate(ys[1:-1], start=1):
            if rect.top + tol < y < rect.bottom - tol:
                warnings.append(
                    TextAssignmentWarning(
                        kind="text_crosses_horizontal_line",
                        message=f"Text span {sid} crosses horizontal grid line {index} at y={y:.2f}",
                        span_id=sid,
                        axis="y",
                        line_index=index,
                        line_coord=y,
                        details={"bbox_px": rect.to_bbox(), "text": _span_text(span)},
                    )
                )
    return warnings


def _candidate_score(span_rect: Rect, cell: Any, rect: Rect) -> tuple[float, str]:
    cx, cy = span_rect.center
    center_inside = rect.contains_point(cx, cy)
    overlap = rect.intersection_area(span_rect)
    overlap_ratio = overlap / span_rect.area if span_rect.area else 0.0
    distance = rect.distance_to_point(cx, cy)
    diagonal = max(math.hypot(rect.width, rect.height), 1.0)
    distance_penalty = min(distance / diagonal, 1.0)
    header_prior = 0.03 if int(_get(cell, "row", 0)) == 0 or bool(_get(cell, "is_column_header", False)) else 0.0
    score = (1.0 if center_inside else 0.0) + overlap_ratio + header_prior - 0.25 * distance_penalty
    method = "center" if center_inside else ("overlap" if overlap > 0 else "nearest")
    return score, method


def assign_span_to_cell(
    span: Any,
    cells: Sequence[Any],
    x_lines: Sequence[Number] | Mapping[str, Number],
    y_lines: Sequence[Number] | Mapping[str, Number],
    *,
    low_confidence_threshold: float = 0.15,
    ambiguous_gap_threshold: float = 0.10,
) -> tuple[Assignment, TextAssignmentWarning | None]:
    """Assign one span to the most plausible current grid cell."""

    if not cells:
        return (
            Assignment(_span_id(span), None, 0.0, "unassigned", None),
            TextAssignmentWarning(
                kind="unassigned_text_span",
                message=f"No cells are available for text span {_span_id(span)}",
                span_id=_span_id(span),
                severity="error",
            ),
        )

    span_rect = Rect.from_bbox(_span_bbox(span))
    scored: list[tuple[float, str, Any]] = []
    for cell in cells:
        try:
            rect = cell_rect(cell, x_lines, y_lines)
        except ValueError:
            continue
        score, method = _candidate_score(span_rect, cell, rect)
        scored.append((score, method, cell))

    if not scored:
        return (
            Assignment(_span_id(span), None, 0.0, "unassigned", None),
            TextAssignmentWarning(
                kind="unassigned_text_span",
                message=f"No valid cell geometry is available for text span {_span_id(span)}",
                span_id=_span_id(span),
                severity="error",
            ),
        )

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, method, best_cell = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else None
    gap = None if second_score is None else best_score - second_score
    confidence = max(0.0, min(1.0, best_score / 2.0))
    assignment = Assignment(_span_id(span), _cell_id(best_cell), confidence, method, gap)

    warning: TextAssignmentWarning | None = None
    if confidence < low_confidence_threshold:
        warning = TextAssignmentWarning(
            kind="low_confidence_text_assignment",
            message=f"Text span {_span_id(span)} assigned to {_cell_id(best_cell)} with low confidence {confidence:.2f}",
            span_id=_span_id(span),
            cell_id=_cell_id(best_cell),
            details={"method": method, "score": best_score, "score_gap": gap},
        )
    elif gap is not None and gap < ambiguous_gap_threshold:
        warning = TextAssignmentWarning(
            kind="ambiguous_text_assignment",
            message=f"Text span {_span_id(span)} is ambiguous between nearby cells (score gap {gap:.2f})",
            span_id=_span_id(span),
            cell_id=_cell_id(best_cell),
            details={"method": method, "score": best_score, "score_gap": gap},
        )
    return assignment, warning


def _median_text_height(spans: Sequence[Any]) -> float:
    heights = sorted(Rect.from_bbox(_span_bbox(span)).height for span in spans if _span_bbox(span))
    if not heights:
        return 0.0
    mid = len(heights) // 2
    if len(heights) % 2:
        return heights[mid]
    return (heights[mid - 1] + heights[mid]) / 2.0


def reconstruct_cell_text(
    spans: Sequence[Any],
    *,
    multiline_separator: str = " ",
    collapse_whitespace: bool = True,
    line_threshold_factor: float = 0.6,
) -> str:
    """Rebuild a cell's text from assigned spans in visual reading order."""

    if not spans:
        return ""
    spans_with_rects = [(span, Rect.from_bbox(_span_bbox(span))) for span in spans]
    spans_with_rects.sort(key=lambda item: (item[1].center[1], item[1].center[0]))
    median_height = _median_text_height(spans) or 1.0
    threshold = median_height * line_threshold_factor

    lines: list[list[tuple[Any, Rect]]] = []
    for span, rect in spans_with_rects:
        cy = rect.center[1]
        if not lines:
            lines.append([(span, rect)])
            continue
        last_line = lines[-1]
        last_cy = sum(item[1].center[1] for item in last_line) / len(last_line)
        if abs(cy - last_cy) <= threshold:
            last_line.append((span, rect))
        else:
            lines.append([(span, rect)])

    rendered_lines: list[str] = []
    for line in lines:
        line.sort(key=lambda item: item[1].center[0])
        rendered_lines.append(" ".join(_span_text(span).strip() for span, _ in line if _span_text(span).strip()))
    text = multiline_separator.join(part for part in rendered_lines if part)
    if collapse_whitespace:
        text = re.sub(r"\s+", " ", text).strip()
    return text


def _cell_key_from_id(cell_id: str | None) -> tuple[int, int, int, int] | None:
    if cell_id is None:
        return None
    match = re.match(r"r(\d+)c(\d+):(\d+)x(\d+)$", str(cell_id))
    if not match:
        return None
    return cast(tuple[int, int, int, int], tuple(int(part) for part in match.groups()))


def _install_model_compat() -> None:
    """Install read-only convenience properties expected by tests/GUI code."""

    try:
        import models as model_mod  # type: ignore
    except Exception:
        return

    table_document = getattr(model_mod, "TableDocument", None)
    if table_document is not None:
        if not hasattr(table_document, "x_edges"):
            setattr(table_document, "x_edges", property(lambda self: self.col_axis.full_edges))
        if not hasattr(table_document, "y_edges"):
            setattr(table_document, "y_edges", property(lambda self: self.row_axis.full_edges))

    text_span = getattr(model_mod, "TextSpan", None)
    if text_span is not None and not hasattr(text_span, "index"):

        def _index(self: Any) -> int:
            if getattr(self, "source_cell_index", None) is not None:
                return int(self.source_cell_index)
            match = re.search(r"(\d+)$", str(getattr(self, "span_id", "0")))
            return int(match.group(1)) if match else 0

        setattr(text_span, "index", property(_index))


_install_model_compat()


def _is_table_document(obj: Any) -> bool:
    return all(hasattr(obj, name) for name in ("cells", "text_spans", "row_axis", "col_axis"))


def _axis_from_document(document: Any) -> tuple[list[float], list[float]]:
    return [float(v) for v in document.col_axis.full_edges], [float(v) for v in document.row_axis.full_edges]


def _assignment_index(span: Any, fallback: int) -> int:
    index = getattr(span, "index", None)
    if index is not None:
        return int(index)
    source = getattr(span, "source_cell_index", None)
    return int(source) if source is not None else fallback


def _assign_text_to_document_in_place(
    document: Any,
    *,
    preserve_manual_text: bool = True,
) -> dict[int, tuple[int, int, int, int] | None]:
    x_lines, y_lines = _axis_from_document(document)
    cells = list(document.cells)
    spans = list(document.text_spans)
    warnings = detect_crossing_warnings(spans, x_lines, y_lines)

    assigned_spans: list[Any] = []
    assignments: dict[int, tuple[int, int, int, int] | None] = {}
    spans_by_cell: dict[tuple[int, int, int, int], list[Any]] = {}
    for fallback_index, span in enumerate(spans):
        assignment, warning = assign_span_to_cell(span, cells, x_lines, y_lines)
        if warning is not None:
            warnings.append(warning)
        cell_key = _cell_key_from_id(assignment.cell_id)
        assignments[_assignment_index(span, fallback_index)] = cell_key
        updated_span = _replace_span(
            span,
            assigned_cell_id=assignment.cell_id,
            confidence=assignment.confidence,
        )
        assigned_spans.append(updated_span)
        if cell_key is not None:
            spans_by_cell.setdefault(cell_key, []).append(updated_span)

    updated_cells: list[Any] = []
    for cell in cells:
        key = (
            int(_get(cell, "row")),
            int(_get(cell, "col")),
            int(_get(cell, "end_row")),
            int(_get(cell, "end_col")),
        )
        cell_spans = spans_by_cell.get(key, [])
        manual = bool(_get(cell, "manual_text_override", False))
        text = _get(cell, "text", "") if preserve_manual_text and manual else reconstruct_cell_text(cell_spans)
        updated_cells.append(
            _replace_record(
                cell,
                text=text,
                assigned_span_ids=tuple(_span_id(span) for span in cell_spans),
            )
        )

    object.__setattr__(document, "cells", tuple(updated_cells))
    object.__setattr__(document, "text_spans", tuple(assigned_spans))
    object.__setattr__(document, "warnings", tuple(warnings))
    return assignments


def assign_text_to_cells(
    cells: Sequence[Any] | Any,
    spans: Sequence[Any] | None = None,
    x_lines: Sequence[Number] | Mapping[str, Number] | None = None,
    y_lines: Sequence[Number] | Mapping[str, Number] | None = None,
    *,
    preserve_manual_text: bool = True,
) -> tuple[list[Any], list[Any], list[TextAssignmentWarning]]:
    """Assign spans to cells and return updated copies plus warnings.

    If *spans* is ``None`` an initial span list is extracted from current cells.
    Manual cells (``manual_text_override=True``) keep their text, but still receive
    source span ids for audit/GUI highlighting. Passing a TableDocument as the
    first argument updates it in place for the PySide command/tests API.
    """

    if x_lines is None and y_lines is None and _is_table_document(cells):
        return _assign_text_to_document_in_place(cells, preserve_manual_text=preserve_manual_text)  # type: ignore[return-value]
    if x_lines is None or y_lines is None:
        raise TypeError("x_lines and y_lines are required when assigning a cell sequence")

    source_spans = list(spans) if spans is not None else extract_text_spans_from_cells(cells)
    warnings = detect_crossing_warnings(source_spans, x_lines, y_lines)

    assigned_spans: list[Any] = []
    spans_by_cell: dict[str, list[Any]] = {_cell_id(cell): [] for cell in cells}
    for span in source_spans:
        assignment, warning = assign_span_to_cell(span, cells, x_lines, y_lines)
        if warning is not None:
            warnings.append(warning)
        updated_span = _replace_span(
            span,
            assigned_cell_id=assignment.cell_id,
            confidence=assignment.confidence,
        )
        assigned_spans.append(updated_span)
        if assignment.cell_id is not None:
            spans_by_cell.setdefault(assignment.cell_id, []).append(updated_span)

    updated_cells: list[Any] = []
    for cell in cells:
        cid = _cell_id(cell)
        cell_spans = spans_by_cell.get(cid, [])
        manual = bool(_get(cell, "manual_text_override", False))
        text = _get(cell, "text", "") if preserve_manual_text and manual else reconstruct_cell_text(cell_spans)
        updates = {
            "text": text,
            "source_span_ids": [_span_id(span) for span in cell_spans],
            "bbox_px": make_cell_bbox(cell, x_lines, y_lines),
            "row_span": int(_get(cell, "end_row")) - int(_get(cell, "row")),
            "col_span": int(_get(cell, "end_col")) - int(_get(cell, "col")),
        }
        updated_cells.append(_replace_record(cell, **updates))

    return updated_cells, assigned_spans, warnings


def _assign_model_text_to_document(document: Any, *, spans: Sequence[Any] | None = None) -> Any:
    """Model-specific text assignment for models.TableDocument.

    Keeps warnings as GridWarning objects so project-state serialization remains
    stable, and updates TableCell.assigned_span_ids/TextSpan.assigned_cell_key.
    """
    from models import TableDocument, detect_crossing_warnings

    if not isinstance(document, TableDocument):
        return None
    source_spans = tuple(spans) if spans is not None else tuple(document.text_spans)
    cell_rects = [document.cell_bbox(cell) for cell in document.cells]
    assignments: dict[str, tuple[int, tuple[int, int, int, int]] | None] = {}

    for span in source_spans:
        bbox = span.bbox
        cx, cy = bbox.center
        best_idx: int | None = None
        for idx, rect in enumerate(cell_rects):
            if rect.contains_point(cx, cy):
                best_idx = idx
                break
        if best_idx is None:
            scored = sorted(((bbox.intersection_area(rect), idx) for idx, rect in enumerate(cell_rects)), reverse=True)
            if scored and scored[0][0] > 0:
                best_idx = scored[0][1]
        if best_idx is None and cell_rects:
            distances = []
            for idx, rect in enumerate(cell_rects):
                rcx, rcy = rect.center
                distances.append((math.hypot(cx - rcx, cy - rcy), idx))
            best_idx = min(distances)[1]
        assignments[span.span_id] = (best_idx, document.cells[best_idx].key) if best_idx is not None else None

    spans_by_cell: dict[tuple[int, int, int, int], list[Any]] = {cell.key: [] for cell in document.cells}
    updated_spans = []
    for span in source_spans:
        assigned = assignments.get(span.span_id)
        key = assigned[1] if assigned else None
        if key is not None:
            spans_by_cell.setdefault(key, []).append(span)
        if hasattr(span, "assigned_cell_key"):
            updated_spans.append(replace(span, assigned_cell_key=key))
        else:
            updated_spans.append(span)

    updated_cells = []
    for cell in document.cells:
        cell_spans = sorted(spans_by_cell.get(cell.key, []), key=lambda sp: sp.reading_order_key if hasattr(sp, "reading_order_key") else (0, 0, _span_id(sp)))
        text = reconstruct_cell_text([
            {"id": getattr(sp, "span_id", _span_id(sp)), "text": getattr(sp, "text", ""), "bbox_px": sp.bbox.to_list() if hasattr(sp.bbox, "to_list") else _span_bbox(sp)}
            for sp in cell_spans
        ])
        updated_cells.append(replace(cell, text=text, assigned_span_ids=tuple(getattr(sp, "span_id", _span_id(sp)) for sp in cell_spans)))
    return replace(
        document,
        cells=tuple(updated_cells),
        text_spans=tuple(updated_spans),
        warnings=detect_crossing_warnings(updated_spans, document.row_axis, document.col_axis),
    )


def assign_text_to_document(document: Any, *, spans: Sequence[Any] | None = None) -> Any:
    """Return a copy of *document* with cells/text_spans/warnings refreshed."""

    model_updated = _assign_model_text_to_document(document, spans=spans)
    if model_updated is not None:
        return model_updated

    from commands import get_axis, get_cells, set_cells_on_copy  # lazy import avoids a cycle

    x_lines = get_axis(document, "x")
    y_lines = get_axis(document, "y")
    cells = get_cells(document)
    existing_spans = list(spans) if spans is not None else list(_get(document, "text_spans", []) or [])
    if not existing_spans:
        existing_spans = extract_text_spans_from_cells(cells)
    updated_cells, assigned_spans, warnings = assign_text_to_cells(cells, existing_spans, x_lines, y_lines)
    updated = set_cells_on_copy(document, updated_cells)
    if _is_mapping(updated) or hasattr(updated, "text_spans"):
        updated = _replace_record(updated, text_spans=assigned_spans)
    existing_warnings = list(_get(updated, "warnings", []) or [])
    # Replace stale text-assignment warnings while preserving unrelated validation warnings.
    unrelated = [w for w in existing_warnings if not str(_get(w, "kind", "")).startswith(("text_", "low_confidence", "ambiguous", "unassigned"))]
    if _is_mapping(updated) or hasattr(updated, "warnings"):
        updated = _replace_record(updated, warnings=unrelated + warnings)
    return updated


@dataclass(frozen=True)
class SpanSplitSuggestion:
    """Approximate split proposal for a span crossed by one grid line."""

    axis: str
    line_index: int
    line_coord: float
    span_id: str
    pieces: tuple[Rect, Rect]
    text_pieces: tuple[str, str]


def _split_text_roughly(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped:
        return "", ""
    midpoint = len(stripped) // 2
    before = stripped.rfind(" ", 0, midpoint)
    after = stripped.find(" ", midpoint)
    if before == -1 and after == -1:
        return stripped, ""
    if before == -1 or (after != -1 and after - midpoint < midpoint - before):
        split_at = after
    else:
        split_at = before
    return stripped[:split_at].strip(), stripped[split_at:].strip()


def suggest_span_splits(document: Any, span: Any) -> list[SpanSplitSuggestion]:
    """Return two-piece split suggestions for grid lines crossing *span*."""

    if _is_table_document(document):
        x_lines, y_lines = _axis_from_document(document)
    else:
        from commands import get_axis  # lazy import avoids a cycle

        x_lines, y_lines = get_axis(document, "x"), get_axis(document, "y")
    rect = Rect.from_bbox(_span_bbox(span))
    left_text, right_text = _split_text_roughly(_span_text(span))
    suggestions: list[SpanSplitSuggestion] = []
    for index, x in enumerate(x_lines[1:-1], start=1):
        tol = _crossing_tolerance(rect)
        if rect.left + tol < x < rect.right - tol:
            suggestions.append(
                SpanSplitSuggestion(
                    axis="x",
                    line_index=index,
                    line_coord=float(x),
                    span_id=_span_id(span),
                    pieces=(
                        Rect(rect.left, rect.top, float(x), rect.bottom),
                        Rect(float(x), rect.top, rect.right, rect.bottom),
                    ),
                    text_pieces=(left_text, right_text),
                )
            )
    top_text, bottom_text = left_text, right_text
    for index, y in enumerate(y_lines[1:-1], start=1):
        tol = _crossing_tolerance(rect)
        if rect.top + tol < y < rect.bottom - tol:
            suggestions.append(
                SpanSplitSuggestion(
                    axis="y",
                    line_index=index,
                    line_coord=float(y),
                    span_id=_span_id(span),
                    pieces=(
                        Rect(rect.left, rect.top, rect.right, float(y)),
                        Rect(rect.left, float(y), rect.right, rect.bottom),
                    ),
                    text_pieces=(top_text, bottom_text),
                )
            )
    return suggestions


def warning_to_dict(warning: TextAssignmentWarning | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(warning, TextAssignmentWarning):
        return asdict(warning)
    return dict(warning)
