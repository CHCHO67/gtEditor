"""PySide6 application shell for the table GT editor MVP."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from html import escape
import json
from pathlib import Path

from commands import (
    AddLineCommand,
    CommandError,
    CommandStack,
    DeleteLineCommand,
    MergeCellsCommand,
    MoveLineCommand,
    UnmergeCellCommand,
)
from graphics_scene import require_qt
from io_docling import (
    InputDataset,
    TablePair,
    discover_input_datasets,
    legacy_input_dataset,
    load_document,
    output_tab_dir,
    save_output_pair,
)
from models import TableCell, TableDocument
from text_assign import assign_text_to_document

_qt = require_qt()
if len(_qt) == 12:
    Qt, QPointF, _Signal, QColor, QPen, QBrush, QPixmap, QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem = _qt
else:
    Qt, QPointF, QColor, QPen, QBrush, QPixmap, QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem = _qt
from PySide6.QtGui import QAction, QCursor, QKeySequence  # noqa: E402
from PySide6.QtCore import QRectF, QSize, QTimer  # noqa: E402
try:  # noqa: E402
    from PySide6.QtPdf import QPdfDocument  # type: ignore
except Exception:  # pragma: no cover - depends on local PySide6 build
    QPdfDocument = None  # type: ignore
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from graphics_scene import TableGraphicsScene  # noqa: E402

STATUS_ORDER = ("review", "accepted_origin", "revision", "discarded")
STATUS_LABELS = {
    "review": "Needs Review",
    "accepted_origin": "Accepted Origin",
    "revision": "Revision",
    "discarded": "Discard",
}
STATUS_BUCKETS = {
    "accepted_origin": "accepted_origin",
    "revision": "revision",
    "discarded": "discarded",
}
ORIGIN_ACCEPT_BUCKETS = ("accepted_origin", "origin_accept", "accepted_original", "saved")
REVISION_BUCKETS = ("revision", "edited", "saved")
STATUS_BUCKET_GROUPS = {
    "accepted_origin": ORIGIN_ACCEPT_BUCKETS,
    "revision": REVISION_BUCKETS,
    "discarded": ("discarded",),
}
BUCKET_STATUSES = {
    "accepted_origin": "accepted_origin",
    "revision": "revision",
    "origin_accept": "accepted_origin",  # legacy output folder kept readable; new saves do not target it.
    "accepted_original": "accepted_origin",  # legacy output folder kept readable; new saves do not target it.
    "edited": "revision",  # legacy output folder kept readable; new saves do not target it.
    "saved": "accepted_origin",  # ambiguous legacy bucket; structural diffs are promoted to Revision at runtime.
    "discarded": "discarded",
}
BUCKET_LABELS = {
    "accepted_origin": "Accepted Origin",
    "revision": "Revision",
    "origin_accept": "Legacy Origin Accept",
    "accepted_original": "Legacy Origin",
    "edited": "Legacy Revision",
    "saved": "Legacy Saved",
    "discarded": "Discard",
}
SORT_BY_NAME = "name"
SORT_BY_MODIFIED = "modified"
SAVE_BUTTON_TEXT = "Save  Ctrl+S"
DISCARD_BUTTON_TEXT = "Discard  Ctrl+D"
PDF_RENDER_DPI = 300.0


class FitToSceneGraphicsView(QGraphicsView):
    """Graphics view that keeps the whole scene visible inside its viewport."""

    def __init__(self, scene):
        super().__init__(scene)
        self._fitting_scene = False

    def resizeEvent(self, event):  # pragma: no cover - Qt event callback
        super().resizeEvent(event)
        self.fit_scene_to_view()

    def showEvent(self, event):  # pragma: no cover - Qt event callback
        super().showEvent(event)
        self.fit_scene_to_view()

    def fit_scene_to_view(self) -> None:
        if self._fitting_scene:
            return
        scene = self.scene()
        if scene is None:
            return
        rect = scene.sceneRect()
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            self.resetTransform()
            return
        if self.viewport().width() <= 0 or self.viewport().height() <= 0:
            return
        self._fitting_scene = True
        try:
            self.resetTransform()
            self.fitInView(rect, Qt.KeepAspectRatio)
            self.centerOn(rect.center())
        finally:
            self._fitting_scene = False


class CellSelectionGraphicsView(FitToSceneGraphicsView):
    """Graphics view that lets reviewers drag directly across cells to select them."""

    def __init__(self, scene):
        super().__init__(scene)
        self._cell_drag_selection_enabled = False
        self._cell_drag_selecting = False
        self._cell_drag_origin = QPointF()

    def set_cell_drag_selection_enabled(self, enabled: bool) -> None:
        self._cell_drag_selection_enabled = bool(enabled)
        if not enabled:
            self._cell_drag_selecting = False
        self._set_scene_cell_selection_enabled(enabled)

    def _cell_selection_requested(self, event) -> bool:
        return bool(
            self._cell_drag_selection_enabled
            or event.modifiers() & Qt.ControlModifier
        )

    def _set_scene_cell_selection_enabled(self, enabled: bool) -> None:
        scene = self.scene()
        if hasattr(scene, "set_cell_selection_enabled"):
            scene.set_cell_selection_enabled(enabled)

    def mousePressEvent(self, event):  # pragma: no cover - Qt event callback
        if self._cell_selection_requested(event) and event.button() == Qt.LeftButton:
            self._set_scene_cell_selection_enabled(True)
            self._cell_drag_selecting = True
            self._cell_drag_origin = self.mapToScene(event.pos())
            if not event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
                self.scene().clearSelection()
            self.select_cells_in_scene_rect(QRectF(self._cell_drag_origin, self._cell_drag_origin))
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            items = self.items(event.pos())
            if any(hasattr(item, "axis") and hasattr(item, "edge_index") for item in items):
                super().mousePressEvent(event)
                return
            if any(hasattr(item, "cell_index") for item in items):
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # pragma: no cover - Qt event callback
        if self._cell_drag_selecting:
            self.select_cells_in_scene_rect(QRectF(self._cell_drag_origin, self.mapToScene(event.pos())))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # pragma: no cover - Qt event callback
        if self._cell_drag_selecting and event.button() == Qt.LeftButton:
            self.select_cells_in_scene_rect(QRectF(self._cell_drag_origin, self.mapToScene(event.pos())))
            self._cell_drag_selecting = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def select_cells_in_scene_rect(self, scene_rect: QRectF) -> None:
        raw_query = scene_rect.normalized()
        query = raw_query.adjusted(-6.0, -6.0, 6.0, 6.0)
        is_click = raw_query.width() < 1.0 and raw_query.height() < 1.0
        click_point = raw_query.center()
        for item in self.scene().items():
            if hasattr(item, "cell_index"):
                item_rect = item.sceneBoundingRect()
                if query.contains(item_rect.center()) or (is_click and item_rect.contains(click_point)):
                    item.setSelected(True)


@dataclass
class DatasetSession:
    """GUI state for one Input_data tab."""

    dataset: InputDataset
    status_tabs: QTabWidget
    lists: dict[str, QListWidget]
    info: QTextBrowser
    statuses: dict[str, str] = field(default_factory=dict)
    sort_mode: str = SORT_BY_NAME
    sort_combo: QComboBox | None = None
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
        self._source_pdf_cache: dict[str, list[Path]] = {}
        self._legacy_saved_status_cache: dict[tuple[str, str], str] = {}
        self._initial_geometry_applied = False
        self._fit_viewers_pending = False

        self.scene = TableGraphicsScene(auto_apply_line_moves=False)
        self.view = CellSelectionGraphicsView(self.scene)
        self.view.setDragMode(QGraphicsView.RubberBandDrag)
        self.original_scene = QGraphicsScene()
        self.original_view = FitToSceneGraphicsView(self.original_scene)
        self.original_view.setObjectName("OriginalView")
        self.original_view.setInteractive(False)
        self.original_view.setMinimumWidth(520)
        self.tabs = QTabWidget()
        self.info = QTextBrowser()
        self.info.setReadOnly(True)
        self.info.setOpenExternalLinks(False)
        self.info.setMinimumHeight(220)
        self.list_widget = QListWidget()
        self.edit_buttons: list[QPushButton] = []

        self.header, self.header_title, self.header_subtitle, self.progress_label = self._build_header()
        self.edit_bar = self._build_edit_bar()
        for dataset in self.datasets:
            self._add_dataset_tab(dataset)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self.tabs, 0)
        content_layout.addWidget(self._build_viewer_splitter(), 1)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)
        root_layout.addWidget(self.header, 0)
        root_layout.addWidget(self.edit_bar, 0)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)
        self.save_toast = QLabel(self)
        self.save_toast.setObjectName("SaveToast")
        self.save_toast.setVisible(False)
        self.scene.documentChanged.connect(self._on_scene_document_changed)
        self.scene.lineMoveRequested.connect(self._on_scene_line_move_requested)
        self._build_shortcuts()
        self._apply_style()
        self.setWindowTitle("gtEditor")
        self.resize(1840, 980)

        if self.sessions:
            self.tabs.setCurrentIndex(0)
            self._select_preferred(self.sessions[0])
        self._update_header()

    def _set_initial_window_geometry(self) -> None:
        """Size and center the window inside the current screen's usable area."""

        target_width = 1840
        target_height = 980
        screen = self._preferred_screen()
        if screen is None:
            self.resize(target_width, target_height)
            return
        window_handle = self.windowHandle()
        if window_handle is not None and window_handle.screen() is not screen:
            window_handle.setScreen(screen)
        available = screen.availableGeometry()
        width = max(1, min(target_width, int(available.width() * 0.96), available.width()))
        height = max(1, min(target_height, int(available.height() * 0.92), available.height()))
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        if frame.left() < available.left():
            frame.moveLeft(available.left())
        if frame.top() < available.top():
            frame.moveTop(available.top())
        self.move(frame.topLeft())

    def _preferred_screen(self):
        """Prefer the screen under the cursor, then this window's screen, then primary."""

        try:
            cursor_screen = QApplication.screenAt(QCursor.pos())
        except Exception:  # pragma: no cover - depends on Qt backend.
            cursor_screen = None
        return cursor_screen or self.screen() or QApplication.primaryScreen()

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
        progress = QLabel("Needs Review 0 · Accepted Origin 0 · Revision 0 · Discard 0")
        progress.setObjectName("ProgressPill")

        text_box = QWidget()
        text_layout = QVBoxLayout(text_box)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        text_layout.addWidget(title)
        text_layout.addWidget(subtitle)

        self.discard_button = QPushButton(DISCARD_BUTTON_TEXT)
        self.discard_button.setObjectName("DiscardButton")
        self.discard_button.setToolTip("현재 파일을 Discard로 분류하고 Output_data에 저장합니다. (Ctrl+D)")
        self.discard_button.clicked.connect(self.discard_current)
        self.save_button = QPushButton(SAVE_BUTTON_TEXT)
        self.save_button.setObjectName("SaveButton")
        self.save_button.setToolTip("현재 파일을 Accepted Origin 또는 Revision으로 분류하고 Output_data에 저장합니다. (Ctrl+S)")
        self.save_button.clicked.connect(self.save_current)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        layout.addWidget(text_box, 1)
        layout.addWidget(progress, 0)
        layout.addWidget(self.discard_button, 0)
        layout.addWidget(self.save_button, 0)
        return header, title, subtitle, progress

    def _build_edit_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("EditBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        mode_label = QLabel("기본 조작: 선 선택 후 드래그 이동")
        mode_label.setObjectName("ModeLabel")
        layout.addWidget(mode_label, 1)

        tool_specs = [
            ("Move Line", "기본", self.activate_line_move_mode, "선을 클릭/드래그해서 이동합니다."),
            ("Select Cells", "C/Ctrl", self.activate_cell_select_mode, "셀 병합을 위해 인접 셀을 드래그/클릭 선택합니다. 선 이동 모드에서는 Ctrl을 누른 채 셀을 선택할 수 있습니다."),
            ("Add V", "V", lambda: self.add_line("x"), "마우스 커서 위치에 세로선을 추가합니다."),
            ("Add H", "H", lambda: self.add_line("y"), "마우스 커서 위치에 가로선을 추가합니다."),
            ("Delete", "D", self.delete_selected_line, "선택한 선을 삭제합니다."),
            ("Merge", "1", self.merge_selected_cells, "선택한 셀들을 병합합니다."),
            ("Unmerge", "2", self.unmerge_selected_cell, "선택한 병합 셀을 해제합니다."),
            ("Undo", "Ctrl+Z", self.undo, "마지막 편집을 되돌립니다."),
        ]
        for label, shortcut, slot, tooltip in tool_specs:
            button = QPushButton(f"{label}  {shortcut}")
            button.setObjectName("MoveToolButton" if label == "Move Line" else "EditToolButton")
            button.setToolTip(tooltip)
            button.clicked.connect(slot)
            layout.addWidget(button, 0)
            self.edit_buttons.append(button)
        return bar

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
        status_tabs.setMinimumHeight(150)
        status_tabs.setMaximumHeight(260)
        lists = {status: QListWidget() for status in STATUS_ORDER}
        info = QTextBrowser()
        info.setReadOnly(True)
        info.setOpenExternalLinks(False)
        info.setMinimumHeight(380)
        info.setObjectName("MarkdownPreview")
        session = DatasetSession(
            dataset=dataset,
            status_tabs=status_tabs,
            lists=lists,
            info=info,
            statuses=self._restore_statuses(dataset),
        )
        sort_combo = QComboBox()
        sort_combo.setObjectName("SortModeCombo")
        sort_combo.addItem("이름순 정렬", SORT_BY_NAME)
        sort_combo.addItem("최근 수정순 정렬", SORT_BY_MODIFIED)
        sort_combo.setToolTip("Accepted Origin/Revision/Discard 목록 정렬 방식입니다. 최근 수정순은 가장 최근 저장한 파일이 위에 옵니다.")
        sort_combo.currentIndexChanged.connect(lambda _index, s=session: self._on_sort_mode_changed(s))
        session.sort_combo = sort_combo
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
        help_label = QLabel("Default: Move Line. Select Cells: drag cells; Ctrl/Shift adds · V/H at cursor · D delete · 1 merge · 2 unmerge · Ctrl+Z undo")
        help_label.setObjectName("ShortcutHelp")
        layout.addWidget(path_label)
        sort_row = QWidget()
        sort_layout = QHBoxLayout(sort_row)
        sort_layout.setContentsMargins(0, 0, 0, 0)
        sort_layout.setSpacing(6)
        sort_label = QLabel("Saved/Discard sort")
        sort_label.setObjectName("SortModeLabel")
        sort_layout.addWidget(sort_label, 0)
        sort_layout.addWidget(sort_combo, 1)
        layout.addWidget(sort_row, 0)
        layout.addWidget(status_tabs, 0)
        preview_label = QLabel("Markdown table preview")
        preview_label.setObjectName("PreviewLabel")
        layout.addWidget(preview_label, 0)
        layout.addWidget(info, 3)
        layout.addWidget(help_label)
        self.tabs.addTab(panel, dataset.name)

    def _build_viewer_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("ViewerSplitter")
        splitter.addWidget(self._viewer_panel("Original", self.original_view, "OriginalPanel"))
        splitter.addWidget(self._viewer_panel("Gridline", self.view, "EditPanel"))
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 900])
        return splitter

    def _viewer_panel(self, title: str, view: QGraphicsView, object_name: str) -> QWidget:
        panel = QFrame()
        panel.setObjectName(object_name)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("ViewerTitle")
        layout.addWidget(label, 0)
        layout.addWidget(view, 1)
        return panel

    def _output_paths(self, dataset: InputDataset, pair: TablePair, bucket: str) -> tuple[Path, Path]:
        bucket_dir = output_tab_dir(self.export_dir, dataset.name) / bucket
        return bucket_dir / "image" / pair.image_path.name, bucket_dir / "json" / pair.json_path.name

    def _bucket_modified_time(self, dataset: InputDataset, pair: TablePair, bucket: str) -> float:
        paths = [path for path in self._output_paths(dataset, pair, bucket) if path.exists()]
        return max((path.stat().st_mtime for path in paths), default=0.0)

    def _bucket_status(self, dataset: InputDataset, pair: TablePair, bucket: str) -> str:
        if bucket == "saved":
            return self._legacy_saved_status(dataset, pair)
        return BUCKET_STATUSES.get(bucket, "accepted_origin")

    def _legacy_saved_status(self, dataset: InputDataset, pair: TablePair) -> str:
        """Infer whether an old unsplit saved/ item was an accepted original or a revision.

        Historical versions had one 검토완료/saved bucket, so the only durable signal
        is whether the saved table structure differs from the input table structure.
        We intentionally ignore cell bbox/layout_tedss formatting because old val
        export normalized those even for unedited accepted originals.
        """

        cache_key = (str(dataset.root), pair.stem)
        cached = self._legacy_saved_status_cache.get(cache_key)
        if cached is not None:
            return cached
        _image_path, json_path = self._output_paths(dataset, pair, "saved")
        status = "accepted_origin"
        try:
            source = self._legacy_structure_signature(pair.json_path)
            saved = self._legacy_structure_signature(json_path)
            status = "revision" if source != saved else "accepted_origin"
        except Exception:  # noqa: BLE001 - best-effort legacy classification.
            status = "accepted_origin"
        self._legacy_saved_status_cache[cache_key] = status
        return status

    def _legacy_structure_signature(self, json_path: Path) -> dict[str, object]:
        record = json.loads(json_path.read_text(encoding="utf-8"))
        cells = record.get("cells", [])
        if not isinstance(cells, list):
            cells = []
        cell_keys = (
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
        )
        return {
            "num_rows": record.get("num_rows"),
            "num_cols": record.get("num_cols"),
            "h_lines": record.get("h_lines"),
            "v_lines": record.get("v_lines"),
            "cells": [
                {key: cell.get(key) for key in cell_keys}
                for cell in cells
                if isinstance(cell, dict)
            ],
        }

    def _latest_bucket_for_status(self, session: DatasetSession, pair: TablePair, status: str) -> str | None:
        buckets = STATUS_BUCKET_GROUPS.get(status, (STATUS_BUCKETS.get(status),))
        candidates = [
            (self._bucket_modified_time(session.dataset, pair, bucket), bucket)
            for bucket in buckets
            if (
                bucket is not None
                and self._output_paths(session.dataset, pair, bucket)[1].exists()
                and self._bucket_status(session.dataset, pair, bucket) == status
            )
        ]
        return max(candidates)[1] if candidates else None

    def _restore_statuses(self, dataset: InputDataset) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for pair in dataset.pairs:
            candidates: list[tuple[float, str]] = []
            for bucket, status in BUCKET_STATUSES.items():
                image_path, json_path = self._output_paths(dataset, pair, bucket)
                if not json_path.exists():
                    continue
                mtime = self._bucket_modified_time(dataset, pair, bucket)
                candidates.append((mtime, self._bucket_status(dataset, pair, bucket)))
            statuses[pair.stem] = max(candidates)[1] if candidates else "review"
        return statuses

    def _output_document_pair(self, session: DatasetSession, pair: TablePair, status: str) -> TablePair:
        bucket = self._latest_bucket_for_status(session, pair, status)
        if bucket is None:
            return pair
        image_path, json_path = self._output_paths(session.dataset, pair, bucket)
        if not json_path.exists():
            return pair
        return TablePair(
            stem=pair.stem,
            image_path=image_path if image_path.exists() else pair.image_path,
            json_path=json_path,
        )

    def _remove_stale_output_pair(self, session: DatasetSession, pair: TablePair, keep_bucket: str) -> None:
        for bucket in BUCKET_STATUSES:
            if bucket == keep_bucket:
                continue
            for path in self._output_paths(session.dataset, pair, bucket):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    def _candidate_page_images(self, pair: TablePair) -> list[Path]:
        """Return possible full-page render paths for a table crop.

        Current local datasets only ship table crops, but keeping this lookup
        lets the Original viewer automatically upgrade to a real page preview
        when future Input_data folders include page-level images.
        """

        if self.doc is None or self.session is None:
            return []
        source_stem = Path(self.doc.source_pdf).stem
        page_no = int(self.doc.page_no)
        roots = [
            self.session.dataset.root,
            self.session.dataset.root.parent,
            pair.image_path.parent,
            pair.image_path.parent.parent,
        ]
        dirs = ("page", "pages", "page_image", "page_images", "pdf_image", "pdf_images", "original", "original_page")
        names = [
            f"{source_stem}_{page_no}",
            f"{source_stem}_{page_no:04d}",
            f"{source_stem}_page_{page_no}",
            f"{source_stem}_page_{page_no:04d}",
            pair.stem,
        ]
        candidates: list[Path] = []
        for root in dict.fromkeys(roots):
            for directory in ("", *dirs):
                base = root / directory if directory else root
                for name in names:
                    for ext in (".png", ".jpg", ".jpeg", ".webp"):
                        path = base / f"{name}{ext}"
                        if path != pair.image_path:
                            candidates.append(path)
        return candidates

    def _candidate_pdf_paths(self) -> list[Path]:
        """Return possible source PDF paths near the active Input_data dataset."""

        if self.doc is None or self.session is None or not self.doc.source_pdf:
            return []
        source_pdf = Path(self.doc.source_pdf)
        names = [source_pdf.name]
        if source_pdf.suffix.lower() != ".pdf":
            names.append(f"{source_pdf.name}.pdf")
        roots = [
            self.session.dataset.root,
            self.session.dataset.root.parent,
            self.session.dataset.root.parent.parent,
            self.session.dataset.image_dir,
            self.session.dataset.json_dir,
        ]
        style_name = self.session.dataset.root.name
        for ancestor in self.session.dataset.root.parents:
            source_dataset_name = ancestor.name.removesuffix("_TTEcrop_passed")
            if source_dataset_name == ancestor.name:
                continue
            projects_root = next((parent for parent in ancestor.parents if parent.name == "projects"), None)
            if projects_root is None:
                continue
            roots.extend(
                [
                    projects_root / "Datasets" / source_dataset_name / style_name,
                    projects_root / "taggedPDF" / "Robin_TTE" / "_noflag_dataset" / style_name,
                    projects_root / "taggedPDF" / "_codex_review2" / "release_rerun_outputs" / style_name,
                ]
            )
        dirs = ("", "pdf", "pdfs", "source_pdf", "source_pdfs", "original", "original_pdf")
        candidates: list[Path] = []
        for root in dict.fromkeys(roots):
            for directory in dirs:
                base = root / directory if directory else root
                for name in names:
                    candidates.append(base / name)
        if source_pdf.is_absolute():
            candidates.insert(0, source_pdf)
        candidates.extend(self._cached_external_pdf_matches(source_pdf.name))
        return candidates

    def _cached_external_pdf_matches(self, pdf_name: str) -> list[Path]:
        """Find source PDFs in common local dataset roots without re-scanning per file."""

        cached = self._source_pdf_cache.get(pdf_name)
        if cached is not None:
            return cached
        matches: list[Path] = []
        projects_root = next((parent for parent in Path.cwd().parents if parent.name == "projects"), None)
        if projects_root is not None:
            for root in (
                projects_root / "Datasets",
                projects_root / "taggedPDF" / "Robin_TTE" / "_noflag_dataset",
                projects_root / "taggedPDF" / "_codex_review2" / "release_rerun_outputs",
            ):
                if root.is_dir():
                    matches.extend(root.rglob(pdf_name))
        self._source_pdf_cache[pdf_name] = matches
        return matches

    def _load_pdf_page_pixmap(self, table_bbox: QRectF) -> tuple[QPixmap, Path] | None:
        """Render the active document's source PDF page if the PDF is present."""

        if QPdfDocument is None or self.doc is None:
            return None
        page_index = max(0, int(self.doc.page_no) - 1)
        for path in self._candidate_pdf_paths():
            if not path.is_file():
                continue
            pdf = QPdfDocument(self)
            error = pdf.load(str(path))
            if error != QPdfDocument.Error.None_ or page_index >= pdf.pageCount():
                continue
            point_size = pdf.pagePointSize(page_index)
            render_width = max(
                1,
                round(float(point_size.width()) * PDF_RENDER_DPI / 72.0),
                round(table_bbox.right()),
            )
            render_height = max(
                1,
                round(float(point_size.height()) * PDF_RENDER_DPI / 72.0),
                round(table_bbox.bottom()),
            )
            render_size = QSize(render_width, render_height)
            image = pdf.render(page_index, render_size)
            if image.isNull():
                continue
            pixmap = QPixmap.fromImage(image)
            if not pixmap.isNull():
                return pixmap, path
        return None

    def _load_page_pixmap(self, pair: TablePair) -> tuple[QPixmap, Path] | None:
        for path in self._candidate_page_images(pair):
            if not path.is_file():
                continue
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return pixmap, path
        return None

    def _table_bbox_for_preview(self) -> QRectF:
        if self.doc is None:
            return QRectF()
        bbox = self.doc.table_bbox_px
        return QRectF(float(bbox.left), float(bbox.top), float(bbox.width), float(bbox.height))

    def _page_canvas_size(self, crop: QPixmap, table_bbox: QRectF, page: QPixmap | None = None) -> tuple[float, float]:
        if page is not None and not page.isNull():
            return float(page.width()), float(page.height())
        width = max(float(crop.width()), table_bbox.right(), table_bbox.left() + float(crop.width()))
        height = max(float(crop.height()), table_bbox.bottom(), table_bbox.top() + float(crop.height()))
        # Many current source pages are 2550x3300-ish while table crops only
        # store crop dimensions in image_size. When bbox coordinates clearly
        # refer to page space, keep enough blank page context around the crop.
        if table_bbox.left() > 1.0 or table_bbox.top() > 1.0:
            if width <= 2600.0 and height <= 3300.0:
                width = max(width, 2550.0)
                height = max(height, 3300.0)
        return width, height

    def _draw_detected_table_bbox(self, rect: QRectF) -> None:
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return
        bbox_item = self.original_scene.addRect(
            rect,
            QPen(QColor(239, 68, 68, 245), 8.0),
            QBrush(QColor(239, 68, 68, 28)),
        )
        bbox_item.setZValue(20)
        bbox_item.setData(0, "detected_table_bbox")
        label = self.original_scene.addSimpleText("Detected table area")
        label.setBrush(QBrush(QColor(127, 29, 29)))
        label.setPos(rect.left() + 12.0, max(0.0, rect.top() - 34.0))
        label.setZValue(21)
        label.setData(0, "detected_table_bbox_label")

    def _set_original_preview(self, pair: TablePair | None) -> None:
        self.original_scene.clear()
        if pair is None:
            self.original_view.fit_scene_to_view()
            return
        crop = QPixmap(str(pair.image_path))
        if crop.isNull():
            self.original_view.fit_scene_to_view()
            return
        table_bbox = self._table_bbox_for_preview()
        pdf_match = self._load_pdf_page_pixmap(table_bbox)
        page_match = pdf_match or self._load_page_pixmap(pair)
        page = page_match[0] if page_match is not None else None
        page_width, page_height = self._page_canvas_size(crop, table_bbox, page)
        page_rect = self.original_scene.addRect(
            QRectF(0.0, 0.0, page_width, page_height),
            QPen(QColor(148, 163, 184), 3.0),
            QBrush(QColor(255, 255, 255)),
        )
        page_rect.setZValue(-10)
        page_rect.setData(0, "page_background")

        if page is not None:
            page_item = self.original_scene.addPixmap(page)
            page_item.setZValue(0)
            page_item.setData(0, "pdf_page_preview" if pdf_match is not None else "page_image_preview")
            page_item.setToolTip(str(page_match[1]))
        else:
            crop_item = self.original_scene.addPixmap(crop)
            crop_item.setZValue(0)
            crop_item.setData(0, "table_crop_on_page_preview")
            crop_item.setToolTip("Full page image not found; showing the crop at table_bbox_px page coordinates.")
            crop_item.setPos(table_bbox.left(), table_bbox.top())

        self._draw_detected_table_bbox(table_bbox)
        self.original_scene.setSceneRect(0.0, 0.0, page_width, page_height)
        self.original_view.fit_scene_to_view()

    def _fit_viewers_to_content(self) -> None:
        """Fit original and editable canvases after scenes/layouts update."""

        if hasattr(self.scene, "set_cell_selection_enabled"):
            self.scene.set_cell_selection_enabled(self.view._cell_drag_selection_enabled)
        if self._fit_viewers_pending:
            return
        self._fit_viewers_pending = True

        def fit_once() -> None:
            self._fit_viewers_pending = False
            self.original_view.fit_scene_to_view()
            self.view.fit_scene_to_view()

        QTimer.singleShot(0, fit_once)

    def _build_shortcuts(self) -> None:
        shortcuts = [
            ("Save current", QKeySequence.Save, self.save_current),
            ("Discard current", "Ctrl+D", self.discard_current),
            ("Select cells", "C", self.activate_cell_select_mode),
            ("Add vertical line", "V", lambda: self.add_line("x")),
            ("Add horizontal line", "H", lambda: self.add_line("y")),
            ("Delete selected line", "D", self.delete_selected_line),
            ("Merge selected cells", "1", self.merge_selected_cells),
            ("Unmerge selected cell", "2", self.unmerge_selected_cell),
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

    def activate_line_move_mode(self) -> None:
        """Keep the default interaction focused on direct grid-line dragging."""

        self.view.setDragMode(QGraphicsView.NoDrag)
        if hasattr(self.view, "set_cell_drag_selection_enabled"):
            self.view.set_cell_drag_selection_enabled(False)
        self.scene.clearSelection()
        self.view.setInteractive(True)
        self.statusBar().showMessage("Line move mode: click a grid line, then drag it. Hold Ctrl to select cells temporarily.", 5000)

    def activate_cell_select_mode(self) -> None:
        """Enable rubber-band cell selection for merge/unmerge operations."""

        self.view.setDragMode(QGraphicsView.RubberBandDrag)
        if hasattr(self.view, "set_cell_drag_selection_enabled"):
            self.view.set_cell_drag_selection_enabled(True)
        self.view.setInteractive(True)
        self.statusBar().showMessage("Cell select mode: drag across cells, then press Merge. Ctrl/Shift keeps previous selection.", 6000)

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
            QLabel#SortModeLabel {
                color: #334155;
                font-size: 12px;
                font-weight: 700;
            }
            QComboBox#SortModeCombo {
                border: 1px solid #cbd5e1;
                border-radius: 7px;
                background: #ffffff;
                color: #0f172a;
                padding: 5px 8px;
            }
            QLabel#PreviewLabel {
                color: #0f172a;
                font-weight: 800;
                margin-top: 6px;
            }
            QTextBrowser#MarkdownPreview {
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                background: #ffffff;
                padding: 6px;
            }
            QFrame#OriginalPanel, QFrame#EditPanel {
                background: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 12px;
            }
            QLabel#ViewerTitle {
                color: #0f172a;
                font-size: 13px;
                font-weight: 900;
            }
            QGraphicsView#OriginalView {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
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
            QFrame#EditBar {
                background: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 12px;
            }
            QLabel#ModeLabel {
                color: #0f172a;
                font-weight: 800;
            }
            QPushButton#EditToolButton, QPushButton#MoveToolButton {
                border: 1px solid #cbd5e1;
                border-radius: 9px;
                color: #0f172a;
                background: #ffffff;
                font-weight: 700;
                padding: 7px 10px;
            }
            QPushButton#EditToolButton:hover {
                background: #e0f2fe;
            }
            QPushButton#MoveToolButton {
                background: #dbeafe;
                border-color: #60a5fa;
                color: #1e3a8a;
            }
            QPushButton#MoveToolButton:hover {
                background: #bfdbfe;
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
            QLabel#SaveToast {
                color: #064e3b;
                background: #bbf7d0;
                border: 2px solid #34d399;
                border-radius: 14px;
                padding: 12px 16px;
                font-size: 15px;
                font-weight: 900;
            }
            """
        )

    def resizeEvent(self, event):  # pragma: no cover - Qt event callback
        super().resizeEvent(event)
        self._position_save_toast()


    def _position_save_toast(self) -> None:
        if not hasattr(self, "save_toast"):
            return
        margin = 22
        self.save_toast.adjustSize()
        x = max(margin, self.width() - self.save_toast.width() - margin)
        y = margin
        self.save_toast.move(x, y)

    def _status_pairs(self, session: DatasetSession, status: str) -> list[TablePair]:
        pairs = [pair for pair in session.dataset.pairs if session.statuses.get(pair.stem, "review") == status]
        if status != "review" and session.sort_mode == SORT_BY_MODIFIED:
            return sorted(pairs, key=lambda pair: (self._status_modified_time(session, pair, status), pair.stem), reverse=True)
        return sorted(pairs, key=lambda pair: pair.stem)

    def _status_modified_time(self, session: DatasetSession, pair: TablePair, status: str) -> float:
        buckets = STATUS_BUCKET_GROUPS.get(status, (STATUS_BUCKETS.get(status),))
        if not buckets:
            return 0.0
        return max(
            (
                self._bucket_modified_time(session.dataset, pair, bucket)
                for bucket in buckets
                if bucket is not None
            ),
            default=0.0,
        )

    def _status_item_text(self, session: DatasetSession, pair: TablePair, status: str) -> str:
        return pair.stem

    def _on_sort_mode_changed(self, session: DatasetSession) -> None:
        combo = session.sort_combo
        if combo is None:
            return
        mode = combo.currentData() or SORT_BY_NAME
        session.sort_mode = str(mode)
        current_stem = self.pair.stem if self.session is session and self.pair is not None else session.current_stem
        if current_stem is not None:
            self._rebuild_status_lists(session, select_stem=current_stem)
        else:
            self._rebuild_status_lists(session)

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
                item = QListWidgetItem(self._status_item_text(session, pair, status))
                item.setData(Qt.UserRole, pair.stem)
                if status in STATUS_BUCKET_GROUPS:
                    bucket = self._latest_bucket_for_status(session, pair, status)
                    if bucket is not None:
                        _, json_path = self._output_paths(session.dataset, pair, bucket)
                        item.setToolTip(f"{BUCKET_LABELS.get(bucket, bucket)} · {json_path}")
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
        self._set_original_preview(None)
        self._fit_viewers_to_content()
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
            source_pair = self._output_document_pair(session, pair, session.statuses.get(pair.stem, "review"))
            doc = assign_text_to_document(load_document(source_pair.image_path, source_pair.json_path))
            stack = CommandStack(doc)
            session.documents[pair.stem] = doc
            session.stacks[pair.stem] = stack
        session.current_stem = pair.stem
        self.pair = pair
        self.doc = doc
        self.stack = stack
        self.scene.set_document(doc)
        self._set_original_preview(pair)
        self._fit_viewers_to_content()
        self.activate_line_move_mode()
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
        self._fit_viewers_to_content()
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
        self._fit_viewers_to_content()
        self.refresh_info()
        self._update_header()

    def _on_scene_line_move_requested(self, axis: str, edge_index: int, coordinate: float) -> None:
        if self.doc is None or self.stack is None:
            return
        try:
            self._do(MoveLineCommand(axis=axis, edge_index=edge_index, coordinate=coordinate))
        except CommandError as exc:
            self.statusBar().showMessage(f"Cannot move line: {exc}", 5000)

    def _cell_text_html(self, cell: TableCell) -> str:
        text = (cell.text or "").strip()
        return escape(text).replace("\n", "<br>") if text else "&nbsp;"

    def _markdown_table_preview_html(self, doc: TableDocument) -> str:
        cells_by_start = {(cell.row, cell.col): cell for cell in doc.cells}
        covered: set[tuple[int, int]] = set()
        rows: list[str] = []
        for row in range(doc.num_rows):
            rendered_cells: list[str] = []
            for col in range(doc.num_cols):
                if (row, col) in covered:
                    continue
                cell = cells_by_start.get((row, col))
                if cell is None:
                    rendered_cells.append("<td class='empty'>&nbsp;</td>")
                    continue
                for rr in range(cell.row, cell.end_row):
                    for cc in range(cell.col, cell.end_col):
                        if (rr, cc) != (row, col):
                            covered.add((rr, cc))
                tag = "th" if cell.is_column_header or cell.is_row_header else "td"
                span_attrs = []
                if cell.row_span > 1:
                    span_attrs.append(f"rowspan='{cell.row_span}'")
                if cell.col_span > 1:
                    span_attrs.append(f"colspan='{cell.col_span}'")
                attrs = " ".join(span_attrs)
                class_name = " class='merged'" if cell.row_span > 1 or cell.col_span > 1 else ""
                rendered_cells.append(f"<{tag}{class_name} {attrs}>{self._cell_text_html(cell)}</{tag}>")
            rows.append(f"<tr>{''.join(rendered_cells)}</tr>")
        status = self.session.statuses.get(doc.stem, "review") if self.session is not None else "review"
        return f"""
        <html>
          <head>
            <style>
              body {{
                font-family: Inter, 'Noto Sans KR', Arial, sans-serif;
                color: #0f172a;
                background: #ffffff;
              }}
              .meta {{
                color: #475569;
                font-size: 12px;
                margin-bottom: 8px;
              }}
              .hint {{
                color: #64748b;
                font-size: 11px;
                margin-bottom: 10px;
              }}
              table {{
                border-collapse: collapse;
                width: 100%;
                table-layout: auto;
                font-size: 12px;
              }}
              td, th {{
                border: 1px solid #94a3b8;
                padding: 5px 7px;
                vertical-align: top;
                min-width: 42px;
                white-space: pre-wrap;
              }}
              th {{
                background: #e0f2fe;
                font-weight: 800;
              }}
              td.merged, th.merged {{
                background: #f3e8ff;
                border: 2px solid #a855f7;
              }}
              td.empty {{
                background: #f8fafc;
                color: #cbd5e1;
              }}
            </style>
          </head>
          <body>
            <div class="meta">
              {escape(doc.stem)} · {STATUS_LABELS[status]} · {doc.num_rows}x{doc.num_cols}
            </div>
            <div class="hint">
              Markdown 추출 미리보기입니다. 병합 셀은 Markdown 표 문법 한계를 보완하기 위해 HTML table의 rowspan/colspan으로 렌더링합니다.
            </div>
            <table>
              {''.join(rows)}
            </table>
          </body>
        </html>
        """

    def refresh_info(self) -> None:
        if self.info is None:
            return
        if self.doc is None:
            self.info.setHtml("<p style='color:#64748b'>No document selected.</p>")
            return
        self.info.setHtml(self._markdown_table_preview_html(self.doc))

    def _update_header(self) -> None:
        has_doc = self.doc is not None and self.session is not None and self.pair is not None
        if self.session is None:
            self.header_title.setText("Input_data")
            self.header_subtitle.setText("No dataset selected")
            self.progress_label.setText("Needs Review 0 · Accepted Origin 0 · Revision 0 · Discard 0")
        else:
            dataset = self.session.dataset
            current = self.pair.stem if self.pair is not None else "No file selected"
            self.header_title.setText(f"Input_data: {dataset.name}")
            self.header_subtitle.setText(f"{dataset.root} · current: {current} · output: {self.export_dir / dataset.name}")
            self.progress_label.setText(
                f"Needs Review {self._status_count(self.session, 'review')} · "
                f"Accepted Origin {self._status_count(self.session, 'accepted_origin')} · "
                f"Revision {self._status_count(self.session, 'revision')} · "
                f"Discard {self._status_count(self.session, 'discarded')}"
            )
        self.save_button.setEnabled(has_doc)
        self.discard_button.setEnabled(has_doc)
        for button in self.edit_buttons:
            button.setEnabled(has_doc)

    def _current_save_bucket(self, status: str) -> str:
        if status == "discarded":
            return "discarded"
        if self.session is None or self.pair is None:
            return STATUS_BUCKETS.get(status, "accepted_origin")
        if self.stack is not None and self.stack.can_undo:
            return "revision"
        if status == "revision":
            return "revision"
        existing_revision_bucket = self._latest_bucket_for_status(self.session, self.pair, "revision")
        if existing_revision_bucket is not None:
            return "revision"
        return "accepted_origin"

    def _status_for_bucket(self, bucket: str) -> str:
        return BUCKET_STATUSES.get(bucket, "accepted_origin")

    def _show_save_confirmation(self, status: str, result_path: Path, bucket: str) -> None:
        """Show a short-lived top-right toast after successful saves."""

        if status == "accepted_origin":
            label = "Accepted Origin saved"
        elif status == "revision":
            label = "Revision saved"
        else:
            label = "Discard saved"
        stem = result_path.stem
        self.save_toast.setText(f"✓ {label}\n{stem}")
        self.save_toast.setToolTip(str(result_path))
        self._position_save_toast()
        self.save_toast.show()
        self.save_toast.raise_()

        def hide_toast() -> None:
            self.save_toast.hide()

        QTimer.singleShot(2200, hide_toast)

    def selected_cells(self) -> list[int | tuple[int, int]]:
        selected: list[int | tuple[int, int]] = []
        for item in self.scene.selectedItems():
            if not hasattr(item, "cell_index") or not hasattr(item, "cell"):
                continue
            if item.cell_index is None:
                selected.append((item.cell.row, item.cell.col))
            else:
                selected.append(int(item.cell_index))
        return selected

    def selected_cell_objects(self) -> list[TableCell]:
        return [item.cell for item in self.scene.selectedItems() if hasattr(item, "cell")]

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
        scene_point = self._cursor_scene_point()
        coord = scene_point.x() if axis == "x" else scene_point.y()
        try:
            self._do(AddLineCommand(axis=axis, coordinate=float(coord)))
        except CommandError as exc:
            QMessageBox.warning(self, "Cannot add line", str(exc))

    def _cursor_scene_point(self) -> QPointF:
        viewport = self.view.viewport()
        cursor_pos = viewport.mapFromGlobal(QCursor.pos())
        if viewport.rect().contains(cursor_pos):
            return self.view.mapToScene(cursor_pos)
        return self.view.mapToScene(viewport.rect().center())

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
        if len(ids) < 2:
            QMessageBox.information(self, "Merge", "Select two or more adjacent cells before merging.")
            return
        try:
            self._do(MergeCellsCommand(selection=self.selected_cell_objects()))
            self.activate_line_move_mode()
        except CommandError as exc:
            QMessageBox.warning(self, "Cannot merge", str(exc))

    def unmerge_selected_cell(self) -> None:
        ids = self.selected_cells()
        if len(ids) != 1:
            QMessageBox.information(self, "Unmerge", "Select exactly one spanned cell.")
            return
        try:
            self._do(UnmergeCellCommand(target=ids[0]))
            self.activate_line_move_mode()
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
        previous_status = self.session.statuses.get(self.pair.stem, "review")
        previous_review_row = self.session.lists["review"].currentRow()
        bucket = self._current_save_bucket(status)
        status = self._status_for_bucket(bucket)
        try:
            result = save_output_pair(self.doc, self.pair, self.export_dir, self.session.dataset.name, bucket=bucket)
        except Exception as exc:  # noqa: BLE001 - GUI boundary surfaces validation/IO errors to users.
            QMessageBox.warning(self, "Save failed", str(exc))
            self.statusBar().showMessage(f"Save failed: {exc}", 7000)
            return
        self.doc = replace(self.doc, image_path=str(result.image_path), json_path=str(result.json_path))
        if self.stack is not None:
            self.stack.document = self.doc
        self._remove_stale_output_pair(self.session, self.pair, keep_bucket=bucket)
        self.session.documents[self.pair.stem] = self.doc
        if self.stack is not None:
            self.session.stacks[self.pair.stem] = self.stack
        self.session.statuses[self.pair.stem] = status
        self.session.current_stem = self.pair.stem
        if previous_status == "review":
            self._rebuild_status_lists(self.session)
            self.session.status_tabs.setCurrentIndex(STATUS_ORDER.index("review"))
            review_list = self.session.lists["review"]
            if review_list.count() > 0:
                review_list.setCurrentRow(min(max(previous_review_row, 0), review_list.count() - 1))
        else:
            self._rebuild_status_lists(self.session, select_stem=self.pair.stem, select_status=status)
        self.statusBar().showMessage(f"{STATUS_LABELS[status]} saved to {result.json_path}", 6000)
        self._show_save_confirmation(status, result.json_path, result.bucket)
        self.refresh_info()
        self._update_header()

    def save_current(self) -> None:
        self._classify_current("accepted_origin")

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
