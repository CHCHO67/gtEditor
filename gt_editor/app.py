"""PySide6 application shell for the table GT editor MVP."""
from __future__ import annotations

from pathlib import Path

from .commands import AddLineCommand, CommandError, CommandStack, DeleteLineCommand, MergeCellsCommand, UnmergeCellCommand
from .graphics_scene import require_qt
from .io_docling import discover_pairs, load_document, save_docling_json, save_project_state
from .text_assign import assign_text_to_document

_qt = require_qt()
if len(_qt) == 12:
    Qt, QPointF, _Signal, QColor, QPen, QBrush, QPixmap, QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem = _qt
else:
    Qt, QPointF, QColor, QPen, QBrush, QPixmap, QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem = _qt
from PySide6.QtGui import QAction, QKeySequence  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFileDialog,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .graphics_scene import TableGraphicsScene  # noqa: E402


class MainWindow(QMainWindow):
    def __init__(self, image_dir: str | Path, json_dir: str | Path, export_dir: str | Path | None = None):
        super().__init__()
        self.image_dir = Path(image_dir)
        self.json_dir = Path(json_dir)
        self.export_dir = Path(export_dir) if export_dir else Path("gt_editor_exports")
        self.pairs = discover_pairs(self.image_dir, self.json_dir)
        self.doc = None
        self.stack: CommandStack | None = None
        self.scene = TableGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setDragMode(QGraphicsView.RubberBandDrag)
        self.list_widget = QListWidget()
        for pair in self.pairs:
            self.list_widget.addItem(pair.stem)
        self.list_widget.currentRowChanged.connect(self.load_index)
        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setMaximumHeight(170)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.addWidget(QLabel("Samples"))
        side_layout.addWidget(self.list_widget)
        side_layout.addWidget(QLabel("Warnings / status"))
        side_layout.addWidget(self.info)
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.addWidget(side, 0)
        layout.addWidget(self.view, 1)
        self.setCentralWidget(root)
        self.scene.documentChanged.connect(self._on_scene_document_changed)
        self._build_actions()
        self.setWindowTitle("Robin TTE GT Editor MVP")
        self.resize(1400, 900)
        if self.pairs:
            self.list_widget.setCurrentRow(0)

    def _build_actions(self) -> None:
        toolbar = QToolBar("GT tools")
        self.addToolBar(toolbar)
        actions = [
            ("Save JSON", QKeySequence.Save, self.save_json),
            ("Save Project", None, self.save_project),
            ("Add V", "V", lambda: self.add_line("x")),
            ("Add H", "H", lambda: self.add_line("y")),
            ("Del Line", "Del", self.delete_selected_line),
            ("Merge", "M", self.merge_selected_cells),
            ("Unmerge", "U", self.unmerge_selected_cell),
            ("Undo", QKeySequence.Undo, self.undo),
        ]
        for label, shortcut, slot in actions:
            act = QAction(label, self)
            if shortcut:
                act.setShortcut(shortcut)
            act.triggered.connect(slot)
            toolbar.addAction(act)

    def load_index(self, index: int) -> None:
        if not (0 <= index < len(self.pairs)):
            return
        pair = self.pairs[index]
        self.doc = assign_text_to_document(load_document(pair.image_path, pair.json_path))
        self.stack = CommandStack(self.doc)
        self.scene.set_document(self.doc)
        self.refresh_info()

    def set_doc(self, doc) -> None:
        self.doc = doc
        if self.stack is not None:
            self.stack.document = doc
        self.scene.set_document(doc)
        self.refresh_info()

    def _on_scene_document_changed(self, document) -> None:
        if self.stack is None:
            return
        self.doc = document
        self.stack.document = document
        self.refresh_info()

    def refresh_info(self) -> None:
        if self.doc is None:
            self.info.setPlainText("No document")
            return
        lines = [
            f"{self.doc.stem}",
            f"grid={self.doc.num_rows}x{self.doc.num_cols} cells={len(self.doc.cells)} spans={len(self.doc.text_spans)} warnings={len(self.doc.warnings)}",
        ]
        lines.extend(f"- {getattr(w, 'message', str(w))}" for w in self.doc.warnings[:20])
        if len(self.doc.warnings) > 20:
            lines.append(f"... {len(self.doc.warnings) - 20} more")
        self.info.setPlainText("\n".join(lines))

    def selected_cells(self) -> list[int]:
        return [int(item.cell_index) for item in self.scene.selectedItems() if hasattr(item, "cell_index")]

    def selected_line(self):
        for item in self.scene.selectedItems():
            if hasattr(item, "axis") and hasattr(item, "edge_index"):
                return item
        return None

    def _do(self, command) -> None:
        if self.stack is None:
            return
        self.set_doc(self.stack.do(command))

    def add_line(self, axis: str) -> None:
        if self.doc is None:
            return
        center = self.view.mapToScene(self.view.viewport().rect().center())
        coord = center.x() if axis == "x" else center.y()
        try:
            self._do(AddLineCommand(axis=axis, coordinate=float(coord)))
        except CommandError as exc:
            QMessageBox.warning(self, "Cannot add line", str(exc))

    def delete_selected_line(self) -> None:
        line = self.selected_line()
        if line is None:
            QMessageBox.information(self, "Delete line", "Select one grid line first.")
            return
        try:
            self._do(DeleteLineCommand(axis=line.axis, line_index=line.edge_index))
        except CommandError as exc:
            QMessageBox.warning(self, "Cannot delete line", str(exc))

    def merge_selected_cells(self) -> None:
        if self.doc is None:
            return
        ids = self.selected_cells()
        if not ids:
            QMessageBox.information(self, "Merge", "Rubber-band or click-select cells first.")
            return
        try:
            self._do(MergeCellsCommand(selection=[self.doc.cells[i] for i in ids]))
        except CommandError as exc:
            QMessageBox.warning(self, "Cannot merge", str(exc))

    def unmerge_selected_cell(self) -> None:
        ids = self.selected_cells()
        if len(ids) != 1:
            QMessageBox.information(self, "Unmerge", "Select exactly one spanned cell.")
            return
        try:
            self._do(UnmergeCellCommand(target=ids[0]))
        except CommandError as exc:
            QMessageBox.warning(self, "Cannot unmerge", str(exc))

    def undo(self) -> None:
        if self.stack is None:
            return
        try:
            self.set_doc(self.stack.undo())
        except CommandError as exc:
            QMessageBox.information(self, "Undo", str(exc))

    def save_json(self) -> None:
        if self.doc is None:
            return
        self.export_dir.mkdir(parents=True, exist_ok=True)
        default = self.export_dir / f"{self.doc.stem}.json"
        path, _ = QFileDialog.getSaveFileName(self, "Save Docling JSON", str(default), "JSON (*.json)")
        if path:
            save_docling_json(self.doc, path)
            self.info.appendPlainText(f"Saved {path}")

    def save_project(self) -> None:
        if self.doc is None:
            return
        self.export_dir.mkdir(parents=True, exist_ok=True)
        default = self.export_dir / f"{self.doc.stem}.gt.json"
        path, _ = QFileDialog.getSaveFileName(self, "Save GT project", str(default), "GT JSON (*.gt.json);;JSON (*.json)")
        if path:
            save_project_state(self.doc, path)
            self.info.appendPlainText(f"Saved {path}")


def build_app(image_dir: str | Path, json_dir: str | Path, export_dir: str | Path | None = None):
    app = QApplication.instance() or QApplication([])
    win = MainWindow(image_dir, json_dir, export_dir)
    return app, win
