"""PySide6 application shell for the table GT editor MVP."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .commands import (
    AddLineCommand,
    CommandError,
    CommandStack,
    DeleteLineCommand,
    MergeCellsCommand,
    MoveLineCommand,
    UnmergeCellCommand,
)
from .graphics_scene import require_qt
from .io_docling import (
    InputDataset,
    TablePair,
    discover_input_datasets,
    legacy_input_dataset,
    load_document,
    save_output_pair,
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
    QFrame,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .graphics_scene import TableGraphicsScene  # noqa: E402

STATUS_ORDER = ("review", "completed", "discarded")
STATUS_LABELS = {
    "review": "검토",
    "completed": "검토 완료",
    "discarded": "버리기",
}


@dataclass
class DatasetSession:
    """GUI state for one Input_data tab."""

    dataset: InputDataset
    status_tabs: QTabWidget
    lists: dict[str, QListWidget]
    info: QPlainTextEdit
    statuses: dict[str, str] = field(default_factory=dict)
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
        self._shortcut_actions: list[QAction] = []

        self.scene = TableGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setDragMode(QGraphicsView.RubberBandDrag)
        self.tabs = QTabWidget()
        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setMaximumHeight(170)
        self.list_widget = QListWidget()

        self.header, self.header_title, self.header_subtitle, self.progress_label = self._build_header()
        for dataset in self.datasets:
            self._add_dataset_tab(dataset)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self.tabs, 0)
        content_layout.addWidget(self.view, 1)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)
        root_layout.addWidget(self.header, 0)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)
        self.scene.documentChanged.connect(self._on_scene_document_changed)
        self._build_shortcuts()
        self._apply_style()
        self.setWindowTitle("gtEditor")
        self.resize(1440, 920)

        if self.sessions:
            self.tabs.setCurrentIndex(0)
            self._select_preferred(self.sessions[0])
        self._update_header()

    @property
    def pairs(self) -> list[TablePair]:
        """Backwards-compatible access to the active tab pairs."""

        return list(self.session.dataset.pairs) if self.session is not None else []

    def _build_header(self):
        header = QFrame()
        header.setObjectName("DecisionHeader")
        title = QLabel("Input_data")
        title.setObjectName("HeaderTitle")
        subtitle = QLabel("No file selected")
        subtitle.setObjectName("HeaderSubtitle")
        progress = QLabel("검토 0 · 검토 완료 0 · 버리기 0")
        progress.setObjectName("ProgressPill")

        text_box = QWidget()
        text_layout = QVBoxLayout(text_box)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        text_layout.addWidget(title)
        text_layout.addWidget(subtitle)

        self.discard_button = QPushButton("Discard  Ctrl+D")
        self.discard_button.setObjectName("DiscardButton")
        self.discard_button.setToolTip("현재 파일을 버리기로 분류하고 Output_data에 저장합니다. (Ctrl+D)")
        self.discard_button.clicked.connect(self.discard_current)
        self.save_button = QPushButton("Save  Ctrl+S")
        self.save_button.setObjectName("SaveButton")
        self.save_button.setToolTip("현재 파일을 검토 완료로 분류하고 Output_data에 저장합니다. (Ctrl+S)")
        self.save_button.clicked.connect(self.save_current)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        layout.addWidget(text_box, 1)
        layout.addWidget(progress, 0)
        layout.addWidget(self.discard_button, 0)
        layout.addWidget(self.save_button, 0)
        return header, title, subtitle, progress

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
        status_tabs = QTabWidget()
        lists = {status: QListWidget() for status in STATUS_ORDER}
        info = QPlainTextEdit()
        info.setReadOnly(True)
        info.setMaximumHeight(170)
        session = DatasetSession(
            dataset=dataset,
            status_tabs=status_tabs,
            lists=lists,
            info=info,
            statuses={pair.stem: "review" for pair in dataset.pairs},
        )
        self.sessions.append(session)

        for status in STATUS_ORDER:
            list_widget = lists[status]
            list_widget.currentRowChanged.connect(
                lambda index, s=session, st=status: self.load_status_index(index, s, st)
            )
            status_tabs.addTab(list_widget, STATUS_LABELS[status])
        self._rebuild_status_lists(session)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        path_label = QLabel(f"Input_data: {dataset.root}")
        path_label.setObjectName("InputPathLabel")
        help_label = QLabel("Shortcuts: V/H add line · Alt+Arrows move line · Del delete · M merge · U unmerge · Ctrl+Z undo")
        help_label.setObjectName("ShortcutHelp")
        layout.addWidget(path_label)
        layout.addWidget(status_tabs, 1)
        layout.addWidget(QLabel("Warnings / status"))
        layout.addWidget(info)
        layout.addWidget(help_label)
        self.tabs.addTab(panel, dataset.name)

    def _build_shortcuts(self) -> None:
        shortcuts = [
            ("Save current", QKeySequence.Save, self.save_current),
            ("Discard current", "Ctrl+D", self.discard_current),
            ("Add vertical line", "V", lambda: self.add_line("x")),
            ("Add horizontal line", "H", lambda: self.add_line("y")),
            ("Delete selected line", "Del", self.delete_selected_line),
            ("Merge selected cells", "M", self.merge_selected_cells),
            ("Unmerge selected cell", "U", self.unmerge_selected_cell),
            ("Undo", QKeySequence.Undo, self.undo),
            ("Move line left", "Alt+Left", lambda: self.nudge_selected_line("x", -1.0)),
            ("Move line right", "Alt+Right", lambda: self.nudge_selected_line("x", 1.0)),
            ("Move line up", "Alt+Up", lambda: self.nudge_selected_line("y", -1.0)),
            ("Move line down", "Alt+Down", lambda: self.nudge_selected_line("y", 1.0)),
            ("Move line left fast", "Shift+Alt+Left", lambda: self.nudge_selected_line("x", -5.0)),
            ("Move line right fast", "Shift+Alt+Right", lambda: self.nudge_selected_line("x", 5.0)),
            ("Move line up fast", "Shift+Alt+Up", lambda: self.nudge_selected_line("y", -5.0)),
            ("Move line down fast", "Shift+Alt+Down", lambda: self.nudge_selected_line("y", 5.0)),
        ]
        for label, shortcut, slot in shortcuts:
            action = QAction(label, self)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ApplicationShortcut)
            action.triggered.connect(slot)
            self.addAction(action)
            self._shortcut_actions.append(action)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QFrame#DecisionHeader {
                background: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 14px;
            }
            QLabel#HeaderTitle {
                color: #f8fafc;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#HeaderSubtitle {
                color: #cbd5e1;
                font-size: 12px;
            }
            QLabel#ProgressPill {
                color: #e2e8f0;
                background: #1e293b;
                border-radius: 11px;
                padding: 6px 10px;
                font-weight: 600;
            }
            QLabel#InputPathLabel {
                color: #334155;
                font-weight: 700;
            }
            QLabel#ShortcutHelp {
                color: #64748b;
                font-size: 11px;
            }
            QPushButton#SaveButton, QPushButton#DiscardButton {
                border: none;
                border-radius: 12px;
                color: white;
                font-size: 14px;
                font-weight: 800;
                padding: 10px 18px;
                min-width: 132px;
            }
            QPushButton#SaveButton {
                background: #10b981;
            }
            QPushButton#SaveButton:hover {
                background: #059669;
            }
            QPushButton#DiscardButton {
                background: #f97316;
            }
            QPushButton#DiscardButton:hover {
                background: #ea580c;
            }
            QPushButton:disabled {
                background: #94a3b8;
                color: #e2e8f0;
            }
            QTabWidget::pane {
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                background: #ffffff;
            }
            QTabBar::tab {
                padding: 7px 12px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #e0f2fe;
                font-weight: 700;
            }
            """
        )

    def _status_pairs(self, session: DatasetSession, status: str) -> list[TablePair]:
        return [pair for pair in session.dataset.pairs if session.statuses.get(pair.stem, "review") == status]

    def _status_count(self, session: DatasetSession, status: str) -> int:
        return sum(1 for value in session.statuses.values() if value == status)

    def _refresh_status_tab_labels(self, session: DatasetSession) -> None:
        for index, status in enumerate(STATUS_ORDER):
            session.status_tabs.setTabText(index, f"{STATUS_LABELS[status]} ({self._status_count(session, status)})")

    def _rebuild_status_lists(
        self,
        session: DatasetSession,
        *,
        select_stem: str | None = None,
        select_status: str | None = None,
    ) -> None:
        for status in STATUS_ORDER:
            list_widget = session.lists[status]
            list_widget.blockSignals(True)
            list_widget.clear()
            for pair in self._status_pairs(session, status):
                item = QListWidgetItem(pair.stem)
                item.setData(Qt.UserRole, pair.stem)
                list_widget.addItem(item)
            list_widget.blockSignals(False)
        self._refresh_status_tab_labels(session)
        if select_stem is not None:
            target_status = select_status or session.statuses.get(select_stem, "review")
            self._select_stem(session, select_stem, target_status)

    def _select_stem(self, session: DatasetSession, stem: str, status: str | None = None) -> bool:
        target_status = status or session.statuses.get(stem, "review")
        list_widget = session.lists[target_status]
        session.status_tabs.setCurrentIndex(STATUS_ORDER.index(target_status))
        for row in range(list_widget.count()):
            if list_widget.item(row).data(Qt.UserRole) == stem:
                list_widget.setCurrentRow(row)
                return True
        return False

    def _select_preferred(self, session: DatasetSession) -> None:
        if session.current_stem and self._select_stem(session, session.current_stem):
            return
        for status in STATUS_ORDER:
            list_widget = session.lists[status]
            if list_widget.count() > 0:
                session.status_tabs.setCurrentIndex(STATUS_ORDER.index(status))
                list_widget.setCurrentRow(0)
                return
        self._clear_document(session)

    def _clear_document(self, session: DatasetSession | None = None) -> None:
        self.session = session
        self.pair = None
        self.doc = None
        self.stack = None
        self.scene.set_document(None)
        self.refresh_info()
        self._update_header()

    def _on_tab_changed(self, index: int) -> None:
        if not (0 <= index < len(self.sessions)):
            return
        session = self.sessions[index]
        self.session = session
        self.info = session.info
        self._select_preferred(session)
        self._update_header()

    def load_status_index(self, index: int, session: DatasetSession, status: str) -> None:
        if index < 0:
            return
        item = session.lists[status].item(index)
        if item is None:
            return
        stem = item.data(Qt.UserRole) or item.text()
        pair = next((candidate for candidate in session.dataset.pairs if candidate.stem == stem), None)
        if pair is not None:
            self.load_pair(pair, session)

    def load_pair(self, pair: TablePair, session: DatasetSession) -> None:
        self.session = session
        tab_index = self.sessions.index(session)
        if self.tabs.currentIndex() != tab_index:
            self.tabs.setCurrentIndex(tab_index)
        self.info = session.info
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
        self._update_header()

    # Backwards-compatible name used by older tests/callers.
    def load_index(self, index: int, session: DatasetSession | None = None) -> None:
        session = session or self.session
        if session is None or not (0 <= index < len(session.dataset.pairs)):
            return
        self.load_pair(session.dataset.pairs[index], session)

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
        self._update_header()

    def _on_scene_document_changed(self, document: TableDocument) -> None:
        if self.stack is None:
            return
        self.doc = document
        self.stack.document = document
        if self.session is not None and self.pair is not None:
            self.session.documents[self.pair.stem] = document
            self.session.stacks[self.pair.stem] = self.stack
        self.refresh_info()
        self._update_header()

    def refresh_info(self) -> None:
        if self.info is None:
            return
        if self.doc is None:
            self.info.setPlainText("No document")
            return
        tab = self.session.dataset.name if self.session is not None else "input"
        status = self.session.statuses.get(self.doc.stem, "review") if self.session is not None else "review"
        lines = [
            f"tab={tab} status={STATUS_LABELS[status]} sample={self.doc.stem}",
            f"grid={self.doc.num_rows}x{self.doc.num_cols} cells={len(self.doc.cells)} spans={len(self.doc.text_spans)} warnings={len(self.doc.warnings)}",
            f"output={self.export_dir / tab}",
            "Shortcuts: V/H add · Alt+Arrow move · Del delete · M merge · U unmerge · Ctrl+Z undo",
        ]
        lines.extend(f"- {getattr(w, 'message', str(w))}" for w in self.doc.warnings[:18])
        if len(self.doc.warnings) > 18:
            lines.append(f"... {len(self.doc.warnings) - 18} more")
        self.info.setPlainText("\n".join(lines))

    def _update_header(self) -> None:
        has_doc = self.doc is not None and self.session is not None and self.pair is not None
        if self.session is None:
            self.header_title.setText("Input_data")
            self.header_subtitle.setText("No dataset selected")
            self.progress_label.setText("검토 0 · 검토 완료 0 · 버리기 0")
        else:
            dataset = self.session.dataset
            current = self.pair.stem if self.pair is not None else "No file selected"
            self.header_title.setText(f"Input_data: {dataset.name}")
            self.header_subtitle.setText(f"{dataset.root} · current: {current} · output: {self.export_dir / dataset.name}")
            self.progress_label.setText(
                f"검토 {self._status_count(self.session, 'review')} · "
                f"검토 완료 {self._status_count(self.session, 'completed')} · "
                f"버리기 {self._status_count(self.session, 'discarded')}"
            )
        self.save_button.setEnabled(has_doc)
        self.discard_button.setEnabled(has_doc)

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

    def nudge_selected_line(self, axis: str, delta: float) -> None:
        if self.doc is None:
            return
        line = self.selected_line()
        if line is None:
            self.statusBar().showMessage("Select one grid line before using Alt+Arrow movement shortcuts.", 4000)
            return
        if line.axis != axis:
            self.statusBar().showMessage("Selected line axis does not match that movement shortcut.", 4000)
            return
        edges = self.doc.x_edges if axis == "x" else self.doc.y_edges
        if not (0 <= line.edge_index < len(edges)):
            return
        try:
            self._do(MoveLineCommand(axis=axis, edge_index=line.edge_index, coordinate=float(edges[line.edge_index]) + delta))
        except CommandError as exc:
            QMessageBox.warning(self, "Cannot move line", str(exc))

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

    def _classify_current(self, status: str) -> None:
        if self.doc is None or self.pair is None or self.session is None:
            return
        try:
            result = save_output_pair(self.doc, self.pair, self.export_dir, self.session.dataset.name)
        except Exception as exc:  # noqa: BLE001 - GUI boundary surfaces validation/IO errors to users.
            QMessageBox.warning(self, "Save failed", str(exc))
            self.info.appendPlainText(f"Save failed: {exc}")
            return
        self.session.documents[self.pair.stem] = self.doc
        if self.stack is not None:
            self.session.stacks[self.pair.stem] = self.stack
        self.session.statuses[self.pair.stem] = status
        self.session.current_stem = self.pair.stem
        self._rebuild_status_lists(self.session, select_stem=self.pair.stem, select_status=status)
        self.info.appendPlainText(f"{STATUS_LABELS[status]}: {result.image_path} and {result.json_path}")
        self.statusBar().showMessage(f"{STATUS_LABELS[status]} saved to {result.json_path}", 6000)
        self.refresh_info()
        self._update_header()

    def save_current(self) -> None:
        self._classify_current("completed")

    def discard_current(self) -> None:
        self._classify_current("discarded")

    # Backwards-compatible action name used by older callers/tests.
    save_json = save_current


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
