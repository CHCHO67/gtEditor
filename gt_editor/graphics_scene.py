"""PySide6 QGraphicsScene overlay for table GT documents."""
from __future__ import annotations

from typing import Callable

from .commands import MoveLineCommand
from .models import TableCell, TableDocument


def require_qt():
    try:
        from PySide6.QtCore import Qt, QPointF, Signal
        from PySide6.QtGui import QColor, QPen, QBrush, QPixmap
        from PySide6.QtWidgets import QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem
    except ImportError as exc:  # pragma: no cover - exercised by CLI environment
        raise RuntimeError("PySide6 is required for the GUI. Run with: uv run --with pyside6 python -m gt_editor.cli ...") from exc
    return Qt, QPointF, Signal, QColor, QPen, QBrush, QPixmap, QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem


Qt, QPointF, Signal, QColor, QPen, QBrush, QPixmap, QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem = require_qt()
from PySide6.QtCore import QTimer  # noqa: E402


class GridLineItem(QGraphicsLineItem):
    def __init__(self, doc: TableDocument, axis: str, edge_index: int, on_changed: Callable[[TableDocument], None] | None = None):
        self.doc = doc
        self.axis = axis
        self.edge_index = edge_index
        self.on_changed = on_changed
        self._resetting_position = False
        if axis == "x":
            x = doc.x_edges[edge_index]
            super().__init__(x, 0, x, doc.height)
        else:
            y = doc.y_edges[edge_index]
            super().__init__(0, y, doc.width, y)
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        color = QColor(0, 210, 80, 210) if axis == "x" else QColor(60, 140, 255, 210)
        self.setPen(QPen(color, 3.0))
        self.setCursor(Qt.SizeHorCursor if axis == "x" else Qt.SizeVerCursor)
        self.setToolTip("Drag this grid line to move it.")
        self.setZValue(10)

    def itemChange(self, change, value):  # pragma: no cover - GUI callback
        if change == QGraphicsItem.ItemPositionChange:
            p = value
            if self.axis == "x":
                return QPointF(float(p.x()), 0.0)
            return QPointF(0.0, float(p.y()))
        if change == QGraphicsItem.ItemPositionHasChanged:
            if self._resetting_position:
                return super().itemChange(change, value)
            pos = self.pos()
            delta = pos.x() if self.axis == "x" else pos.y()
            if abs(float(delta)) < 0.01:
                return super().itemChange(change, value)
            coord = self.doc.x_edges[self.edge_index] + pos.x() if self.axis == "x" else self.doc.y_edges[self.edge_index] + pos.y()
            try:
                updated = MoveLineCommand(axis=self.axis, line_index=self.edge_index, coordinate=coord).apply(self.doc)
                self.doc = updated
                self._resetting_position = True
                try:
                    self.setPos(0, 0)
                finally:
                    self._resetting_position = False
                if self.on_changed:
                    # Do not rebuild the scene while Qt is still inside this
                    # item's movement notification. Deleting/recreating this
                    # item synchronously from itemChange can segfault Qt.
                    QTimer.singleShot(0, lambda doc=updated, callback=self.on_changed: callback(doc))
            except Exception:
                self._resetting_position = True
                try:
                    self.setPos(0, 0)
                finally:
                    self._resetting_position = False
        return super().itemChange(change, value)


class CellRectItem(QGraphicsRectItem):
    def __init__(self, cell_index: int, cell: TableCell, rect):
        if hasattr(rect, "to_list"):
            rect = rect.to_list()
        x0, y0, x1, y1 = rect
        super().__init__(float(x0), float(y0), float(x1) - float(x0), float(y1) - float(y0))
        self.cell_index = cell_index
        self.cell = cell
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setPen(QPen(QColor(255, 170, 0, 120), 0.8))
        self.setBrush(QBrush(QColor(255, 200, 0, 18)))
        self.setZValue(4)
        self.setToolTip(f"cell#{cell_index} r{cell.row}:{cell.end_row} c{cell.col}:{cell.end_col}\n{cell.text[:200]}")


class TableGraphicsScene(QGraphicsScene):
    documentChanged = Signal(object)

    def __init__(self, doc: TableDocument | None = None):
        super().__init__()
        self.doc: TableDocument | None = None
        if doc is not None:
            self.set_document(doc)

    def set_document(self, doc: TableDocument) -> None:
        self.doc = doc
        self.rebuild()

    def _replace_document_from_item(self, doc: TableDocument) -> None:
        self.doc = doc
        self.rebuild()
        self.documentChanged.emit(doc)

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
            if cell.text:
                t = QGraphicsSimpleTextItem(cell.text[:80])
                t.setPos(rect_values[0] + 2, rect_values[1] + 1)
                t.setBrush(QBrush(QColor(20, 20, 20, 190)))
                t.setZValue(8)
                self.addItem(t)
        for i in range(1, len(doc.x_edges) - 1):
            self.addItem(GridLineItem(doc, "x", i, self._replace_document_from_item))
        for i in range(1, len(doc.y_edges) - 1):
            self.addItem(GridLineItem(doc, "y", i, self._replace_document_from_item))
        warning_span_ids = {getattr(w, "span_id", getattr(w, "span_index", None)) for w in doc.warnings}
        for span in doc.text_spans:
            bbox = span.bbox.to_list() if hasattr(span.bbox, "to_list") else list(span.bbox)
            x0, y0, x1, y1 = bbox
            item = QGraphicsRectItem(float(x0), float(y0), float(x1) - float(x0), float(y1) - float(y0))
            span_id = getattr(span, "span_id", getattr(span, "index", None))
            color = QColor(255, 0, 0, 185) if span_id in warning_span_ids else QColor(120, 0, 180, 95)
            item.setPen(QPen(color, 1.0))
            item.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 22)))
            item.setZValue(12)
            item.setToolTip(f"span {span_id} -> cell {getattr(span, 'assigned_cell_key', None)}\n{span.text[:200]}")
            self.addItem(item)
