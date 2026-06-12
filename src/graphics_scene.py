"""PySide6 QGraphicsScene overlay for table GT documents."""
from __future__ import annotations

from typing import Callable

from commands import MoveLineCommand
from models import TableCell, TableDocument


def require_qt():
    try:
        from PySide6.QtCore import Qt, QPointF, Signal
        from PySide6.QtGui import QColor, QPen, QBrush, QPixmap
        from PySide6.QtWidgets import QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem
    except ImportError as exc:  # pragma: no cover - exercised by CLI environment
        raise RuntimeError("PySide6 is required for the GUI. Run with: uv run --with pyside6 python -m cli ...") from exc
    return Qt, QPointF, Signal, QColor, QPen, QBrush, QPixmap, QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem


Qt, QPointF, Signal, QColor, QPen, QBrush, QPixmap, QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem = require_qt()
from PySide6.QtCore import QTimer  # noqa: E402


class GridLineItem(QGraphicsLineItem):
    def __init__(self, doc: TableDocument, axis: str, edge_index: int, on_move_requested: Callable[[str, int, float], None] | None = None):
        self.doc = doc
        self.axis = axis
        self.edge_index = edge_index
        self.on_move_requested = on_move_requested
        self._resetting_position = False
        if axis == "x":
            x = doc.x_edges[edge_index]
            super().__init__(x, 0, x, doc.height)
        else:
            y = doc.y_edges[edge_index]
            super().__init__(0, y, doc.width, y)
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        color = QColor(239, 68, 68, 220) if axis == "x" else QColor(60, 140, 255, 210)
        self.setPen(QPen(color, 3.0))
        self.setCursor(Qt.SizeHorCursor if axis == "x" else Qt.SizeVerCursor)
        self.setToolTip("Drag this grid line to move it.")
        self.setZValue(10)

    def _movement_delta(self) -> float:
        pos = self.pos()
        return float(pos.x() if self.axis == "x" else pos.y())

    def _reset_position(self) -> None:
        self._resetting_position = True
        try:
            self.setPos(0, 0)
        finally:
            self._resetting_position = False

    def commit_pending_move(self) -> None:
        """Commit a drag after the user releases the line.

        During a drag the Qt item is allowed to move visually. Committing on
        every intermediate position forces a scene rebuild and makes dragging
        feel like step-wise nudging; it can also delete this item while Qt is
        still handling its move event. Commit once on release instead.
        """

        delta = self._movement_delta()
        if abs(delta) < 0.01:
            self._reset_position()
            return
        base = self.doc.x_edges[self.edge_index] if self.axis == "x" else self.doc.y_edges[self.edge_index]
        coordinate = float(base) + delta
        try:
            MoveLineCommand(axis=self.axis, line_index=self.edge_index, coordinate=coordinate).apply(self.doc)
        except Exception:
            self._reset_position()
            return
        self._reset_position()
        if self.on_move_requested:
            # Do not rebuild the scene while Qt is still inside this item's
            # mouse-release notification. Deleting/recreating this item
            # synchronously from a graphics-item callback can segfault Qt.
            callback = self.on_move_requested
            axis = self.axis
            edge_index = self.edge_index
            QTimer.singleShot(0, lambda: callback(axis, edge_index, coordinate))

    def itemChange(self, change, value):  # pragma: no cover - GUI callback
        if change == QGraphicsItem.ItemPositionChange:
            p = value
            if self.axis == "x":
                return QPointF(float(p.x()), 0.0)
            return QPointF(0.0, float(p.y()))
        if change == QGraphicsItem.ItemPositionHasChanged:
            if self._resetting_position:
                return super().itemChange(change, value)
            # Leave the visual item under the cursor during drag; the final
            # document mutation happens in mouseReleaseEvent().
            return super().itemChange(change, value)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):  # pragma: no cover - GUI callback
        super().mouseReleaseEvent(event)
        self.commit_pending_move()


class CellRectItem(QGraphicsRectItem):
    def __init__(self, cell_index: int | None, cell: TableCell, rect, *, is_virtual: bool = False):
        if hasattr(rect, "to_list"):
            rect = rect.to_list()
        x0, y0, x1, y1 = rect
        super().__init__(float(x0), float(y0), float(x1) - float(x0), float(y1) - float(y0))
        self.cell_index = cell_index
        self.cell = cell
        self.is_virtual = is_virtual
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self._is_merged = getattr(cell, "row_span", 1) > 1 or getattr(cell, "col_span", 1) > 1
        self._apply_selection_style()
        merged_label = "merged " if self._is_merged else ""
        virtual_label = "implicit empty " if is_virtual else ""
        self.setToolTip(f"{merged_label}{virtual_label}cell#{cell_index} r{cell.row}:{cell.end_row} c{cell.col}:{cell.end_col}\n{cell.text[:200]}")

    def set_selection_enabled(self, enabled: bool) -> None:
        self.setFlag(QGraphicsItem.ItemIsSelectable, enabled)

    def _apply_selection_style(self) -> None:
        if self.isSelected():
            self.setPen(QPen(QColor(2, 132, 199, 245), 3.0))
            self.setBrush(QBrush(QColor(56, 189, 248, 96)))
            self.setZValue(9)
        elif self._is_merged:
            self.setPen(QPen(QColor(126, 34, 206, 210), 2.2))
            self.setBrush(QBrush(QColor(168, 85, 247, 72)))
            self.setZValue(6)
        elif self.is_virtual:
            self.setPen(QPen(QColor(245, 158, 11, 72), 0.6))
            self.setBrush(QBrush(QColor(255, 255, 255, 0)))
            self.setZValue(3)
        else:
            self.setPen(QPen(QColor(255, 170, 0, 120), 0.8))
            self.setBrush(QBrush(QColor(255, 200, 0, 18)))
            self.setZValue(4)

    def itemChange(self, change, value):  # pragma: no cover - GUI callback
        result = super().itemChange(change, value)
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self._apply_selection_style()
        return result


class TableGraphicsScene(QGraphicsScene):
    documentChanged = Signal(object)
    lineMoveRequested = Signal(str, int, float)

    def __init__(self, doc: TableDocument | None = None, *, auto_apply_line_moves: bool = True):
        super().__init__()
        self.doc: TableDocument | None = None
        if auto_apply_line_moves:
            self.lineMoveRequested.connect(self._apply_requested_line_move)
        if doc is not None:
            self.set_document(doc)

    def _request_line_move(self, axis: str, edge_index: int, coordinate: float) -> None:
        self.lineMoveRequested.emit(axis, edge_index, coordinate)

    def _apply_requested_line_move(self, axis: str, edge_index: int, coordinate: float) -> None:
        if self.doc is None:
            return
        try:
            updated = MoveLineCommand(axis=axis, line_index=edge_index, coordinate=coordinate).apply(self.doc)
        except Exception:
            return
        self._replace_document_from_item(updated)

    def set_document(self, doc: TableDocument) -> None:
        self.doc = doc
        self.rebuild()

    def _replace_document_from_item(self, doc: TableDocument) -> None:
        self.doc = doc
        self.rebuild()
        self.documentChanged.emit(doc)

    def set_cell_selection_enabled(self, enabled: bool) -> None:
        for item in self.items():
            if isinstance(item, CellRectItem):
                item.set_selection_enabled(enabled)

    def rebuild(self) -> None:
        self.clear()
        if self.doc is None:
            return
        doc = self.doc
        self.setSceneRect(0, 0, doc.width, doc.height)
        if doc.image_path:
            pix = QPixmap(str(doc.image_path))
            if not pix.isNull():
                item = self.addPixmap(pix)
                item.setZValue(0)
        for idx, cell in enumerate(doc.cells):
            rect = doc.cell_bbox(cell)
            rect_values = rect.to_list() if hasattr(rect, "to_list") else rect
            self.addItem(CellRectItem(idx, cell, rect_values))
        covered_slots = {
            (row, col)
            for cell in doc.cells
            for row in range(cell.row, cell.end_row)
            for col in range(cell.col, cell.end_col)
        }
        for row in range(doc.num_rows):
            for col in range(doc.num_cols):
                if (row, col) in covered_slots:
                    continue
                cell = TableCell(row=row, col=col, end_row=row + 1, end_col=col + 1, metadata={"virtual": True})
                rect = doc.cell_bbox(cell)
                rect_values = rect.to_list() if hasattr(rect, "to_list") else rect
                self.addItem(CellRectItem(None, cell, rect_values, is_virtual=True))
        for i in range(1, len(doc.x_edges) - 1):
            self.addItem(GridLineItem(doc, "x", i, self._request_line_move))
        for i in range(1, len(doc.y_edges) - 1):
            self.addItem(GridLineItem(doc, "y", i, self._request_line_move))
        for span in doc.text_spans:
            bbox = span.bbox.to_list() if hasattr(span.bbox, "to_list") else list(span.bbox)
            x0, y0, x1, y1 = bbox
            item = QGraphicsRectItem(float(x0), float(y0), float(x1) - float(x0), float(y1) - float(y0))
            span_id = getattr(span, "span_id", getattr(span, "index", None))
            item.setData(0, "text_box")
            item.setPen(QPen(QColor(22, 163, 74, 220), 1.8))
            item.setBrush(QBrush(QColor(34, 197, 94, 42)))
            item.setZValue(7)
            item.setToolTip(f"span {span_id} -> cell {getattr(span, 'assigned_cell_key', None)}\n{span.text[:200]}")
            self.addItem(item)
