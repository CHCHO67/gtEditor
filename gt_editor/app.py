"""PySide6 application shell for the table GT editor MVP."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .commands import AddLineCommand, CommandError, CommandStack, DeleteLineCommand, MergeCellsCommand, UnmergeCellCommand
from .graphics_scene import require_qt
from .io_docling import (
    InputDataset,
    TablePair,
    discover_input_datasets,
    legacy_input_dataset,
    load_document,
    save_output_pair,
    save_output_tab,
    save_project_state,
    verify_output_tab_counts,
)
from .models import TableDocument
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
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .graphics_scene import TableGraphicsScene  # noqa: E402


@dataclass
class DatasetSession:
    """GUI state for one Input_data tab."""

    dataset: InputDataset
    list_widget: QListWidget
    info: QPlainTextEdit
    documents: dict[str, TableDocument] = field(default_factory=dict)
    stacks: dict[str, CommandStack] = field(default_factory=dict)
    current_stem: str | None = None


class MainWindow(QMainWindow):
    def __init__(
        self,
        image_dir: str | Path | None = None,
        json_dir: str | Path | None = None,
        export_dir: str | Path | None = None,
        *,
        input_data: list[str | Path] | None = None,
        output_data: str | Path | None = None,
    ):
        super().__init__()
        self.export_dir = Path(output_data or export_dir or "Output_data")
        self.datasets = self._resolve_datasets(image_dir, json_dir, input_data)
        self.sessions: list[DatasetSession] = []
        self.session: DatasetSession | None = None
        self.pair: TablePair | None = None
        self.doc: TableDocument | None = None
        self.stack: CommandStack | None = None

        self.scene = TableGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setDragMode(QGraphicsView.RubberBandDrag)
        self.tabs = QTabWidget()
        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setMaximumHeight(170)
        self.list_widget = QListWidget()

        for dataset in self.datasets:
            self._add_dataset_tab(dataset)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.addWidget(self.tabs, 0)
        layout.addWidget(self.view, 1)
        self.setCentralWidget(root)
        self.scene.documentChanged.connect(self._on_scene_document_changed)
        self._build_actions()
        self.setWindowTitle("gtEditor")
        self.resize(1400, 900)

        if self.sessions:
            self.tabs.setCurrentIndex(0)
            if self.sessions[0].dataset.pairs:
                self.sessions[0].list_widget.setCurrentRow(0)
            else:
                self._on_tab_changed(0)

    @property
    def pairs(self) -> list[TablePair]:
        """Backwards-compatible access to the active tab pairs."""

        return list(self.session.dataset.pairs) if self.session is not None else []

    def _resolve_datasets(
        self,
        image_dir: str | Path | None,
        json_dir: str | Path | None,
        input_data: list[str | Path] | None,
    ) -> list[InputDataset]:
        if input_data:
            return discover_input_datasets(input_data)
        return [legacy_input_dataset(image_dir or "gt_editor_samples/image", json_dir or "gt_editor_samples/json")]

    def _add_dataset_tab(self, dataset: InputDataset) -> None:
        list_widget = QListWidget()
        for pair in dataset.pairs:
            list_widget.addItem(pair.stem)
        info = QPlainTextEdit()
        info.setReadOnly(True)
        info.setMaximumHeight(170)
        session = DatasetSession(dataset=dataset, list_widget=list_widget, info=info)
        self.sessions.append(session)
        list_widget.currentRowChanged.connect(lambda index, s=session: self.load_index(index, s))

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel(f"Input_data: {dataset.root}"))
        layout.addWidget(QLabel("Samples"))
        layout.addWidget(list_widget)
        layout.addWidget(QLabel("Warnings / status"))
        layout.addWidget(info)
        self.tabs.addTab(panel, dataset.name)

    def _build_actions(self) -> None:
        toolbar = QToolBar("GT tools")
        self.addToolBar(toolbar)
        actions = [
            ("Save Current", QKeySequence.Save, self.save_current),
            ("Save Tab", None, self.save_tab),
            ("Save All", None, self.save_all),
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

    def _on_tab_changed(self, index: int) -> None:
        if not (0 <= index < len(self.sessions)):
            return
        session = self.sessions[index]
        self.session = session
        self.list_widget = session.list_widget
        self.info = session.info
        row = session.list_widget.currentRow()
        if row < 0 and session.dataset.pairs:
            session.list_widget.setCurrentRow(0)
            return
        self.load_index(row, session)

    def load_index(self, index: int, session: DatasetSession | None = None) -> None:
        session = session or self.session
        if session is None or not (0 <= index < len(session.dataset.pairs)):
            return
        self.session = session
        tab_index = self.sessions.index(session)
        if self.tabs.currentIndex() != tab_index:
            self.tabs.setCurrentIndex(tab_index)
        self.list_widget = session.list_widget
        self.info = session.info
        pair = session.dataset.pairs[index]
        doc = session.documents.get(pair.stem)
        stack = session.stacks.get(pair.stem)
        if doc is None or stack is None:
            doc = assign_text_to_document(load_document(pair.image_path, pair.json_path))
            stack = CommandStack(doc)
            session.documents[pair.stem] = doc
            session.stacks[pair.stem] = stack
        session.current_stem = pair.stem
        self.pair = pair
        self.doc = doc
        self.stack = stack
        self.scene.set_document(doc)
        self.refresh_info()

    def set_doc(self, doc: TableDocument) -> None:
        self.doc = doc
        if self.stack is not None:
            self.stack.document = doc
        if self.session is not None and self.pair is not None:
            self.session.documents[self.pair.stem] = doc
            self.session.stacks[self.pair.stem] = self.stack or CommandStack(doc)
            self.session.current_stem = self.pair.stem
        self.scene.set_document(doc)
        self.refresh_info()

    def _on_scene_document_changed(self, document: TableDocument) -> None:
        if self.stack is None:
            return
        self.doc = document
        self.stack.document = document
        if self.session is not None and self.pair is not None:
            self.session.documents[self.pair.stem] = document
            self.session.stacks[self.pair.stem] = self.stack
        self.refresh_info()

    def refresh_info(self) -> None:
        if self.doc is None:
            self.info.setPlainText("No document")
            return
        tab = self.session.dataset.name if self.session is not None else "input"
        lines = [
            f"tab={tab} sample={self.doc.stem}",
            f"grid={self.doc.num_rows}x{self.doc.num_cols} cells={len(self.doc.cells)} spans={len(self.doc.text_spans)} warnings={len(self.doc.warnings)}",
            f"output={self.export_dir / tab}",
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

    def save_current(self) -> None:
        if self.doc is None or self.pair is None or self.session is None:
            return
        try:
            result = save_output_pair(self.doc, self.pair, self.export_dir, self.session.dataset.name)
        except Exception as exc:  # noqa: BLE001 - GUI boundary surfaces validation/IO errors to users.
            QMessageBox.warning(self, "Save current failed", str(exc))
            self.info.appendPlainText(f"Save failed: {exc}")
            return
        self.info.appendPlainText(f"Saved current: {result.image_path} and {result.json_path}")
        self.statusBar().showMessage(f"Saved current to {result.json_path}", 5000)

    def save_tab(self) -> None:
        if self.session is None:
            return
        self._save_sessions([self.session], label="tab")

    def save_all(self) -> None:
        self._save_sessions(self.sessions, label="all tabs")

    def _save_sessions(self, sessions: list[DatasetSession], *, label: str) -> None:
        saved = 0
        count_lines: list[str] = []
        errors: list[str] = []
        for session in sessions:
            try:
                results = save_output_tab(session.dataset, self.export_dir, documents=session.documents)
                saved += len(results)
                ok, counts = verify_output_tab_counts(self.export_dir, session.dataset)
                count_lines.append(counts)
                if not ok:
                    errors.append(counts)
            except Exception as exc:  # noqa: BLE001 - aggregate GUI save errors for user/status.
                errors.append(f"{session.dataset.name}: {exc}")
        message = f"Saved {saved} document(s) for {label} to {self.export_dir}"
        if count_lines:
            message += "; " + "; ".join(count_lines)
        if errors:
            QMessageBox.warning(self, "Save errors", "\n".join(errors[:10]))
            message += f"; {len(errors)} error(s)"
        self.info.appendPlainText(message)
        for error in errors[:20]:
            self.info.appendPlainText(f"- {error}")
        self.statusBar().showMessage(message, 7000)

    # Backwards-compatible action name used by older callers/tests.
    save_json = save_current

    def save_project(self) -> None:
        if self.doc is None:
            return
        self.export_dir.mkdir(parents=True, exist_ok=True)
        default = self.export_dir / f"{self.doc.stem}.gt.json"
        path, _ = QFileDialog.getSaveFileName(self, "Save GT project", str(default), "GT JSON (*.gt.json);;JSON (*.json)")
        if path:
            save_project_state(self.doc, path)
            self.info.appendPlainText(f"Saved {path}")


def build_app(
    image_dir: str | Path | None = None,
    json_dir: str | Path | None = None,
    export_dir: str | Path | None = None,
    *,
    input_data: list[str | Path] | None = None,
    output_data: str | Path | None = None,
):
    app = QApplication.instance() or QApplication([])
    win = MainWindow(image_dir, json_dir, export_dir, input_data=input_data, output_data=output_data)
    return app, win
