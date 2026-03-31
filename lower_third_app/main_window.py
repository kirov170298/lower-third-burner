from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTime, Signal
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .ffmpeg_utils import (
    FFmpegError,
    export_video,
    extract_preview_frame,
    find_font_path,
    get_video_metadata,
    output_extension_for_format,
    temporary_preview_image,
    validate_export_settings,
)
from .models import RenderSettings, TitleEntry, VideoMetadata
from .preview import PreviewView


class ExportWorker(QObject):
    progress_changed = Signal(int)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        video_path: str,
        graphic_path: str | None,
        entries: list[TitleEntry],
        output_path: str,
        settings: RenderSettings,
    ) -> None:
        super().__init__()
        self.video_path = video_path
        self.graphic_path = graphic_path
        self.entries = entries
        self.output_path = output_path
        self.settings = settings

    def run(self) -> None:
        try:
            export_video(
                self.video_path,
                self.graphic_path,
                self.entries,
                self.output_path,
                self.settings,
                progress_callback=self.progress_changed.emit,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(self.output_path)


def seconds_to_qtime(value: float) -> QTime:
    total_ms = max(0, int(round(value * 1000)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    seconds = (total_ms % 60_000) // 1000
    milliseconds = total_ms % 1000
    return QTime(hours % 24, minutes, seconds, milliseconds)


def qtime_to_seconds(value: QTime) -> float:
    midnight = QTime(0, 0, 0, 0)
    return midnight.msecsTo(value) / 1000.0


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.video_path: str | None = None
        self.graphic_path: str | None = None
        self.video_metadata: VideoMetadata | None = None
        self.preview_frame_path: str | None = None
        self.entries: list[TitleEntry] = []
        self._loading_entry = False
        self._loading_project = False
        self._syncing_preview_position = False
        self._default_first_appearance_seconds = 20.0
        self._default_first_duration_seconds = 5.0
        self._default_second_offset_seconds = 10.0
        self._default_second_duration_seconds = 5.0
        self._font_color = "#FFFFFF"
        self._export_thread: QThread | None = None
        self._export_worker: ExportWorker | None = None
        self._project_path: Path | None = None
        self._project_dirty = False
        self.settings_store = QSettings("Valentin Kirov", "Lower Third Burner")

        self.resize(1400, 900)

        self.video_path_edit = QLineEdit()
        self.video_path_edit.setReadOnly(True)
        self.graphic_path_edit = QLineEdit()
        self.graphic_path_edit.setReadOnly(True)
        self.graphic_path_edit.setPlaceholderText("Optional PNG overlay")
        self.export_dir_edit = QLineEdit()
        self.export_dir_edit.setReadOnly(True)
        self.export_dir_edit.setPlaceholderText("Uses the source video folder by default")

        self.resolution_combo = QComboBox()
        self._populate_resolution_options()
        self.resolution_combo.currentIndexChanged.connect(self._on_resolution_changed)

        self.export_format_combo = QComboBox()
        self._populate_export_format_options()
        self.export_format_combo.currentIndexChanged.connect(self._on_render_settings_changed)

        self.preview = PreviewView()
        self.preview.text_position_changed.connect(self._on_preview_text_dragged)
        self.entry_list = QListWidget()
        self.entry_list.currentRowChanged.connect(self._on_entry_selected)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Type the lower-third text here.")
        self.text_edit.textChanged.connect(self._save_current_entry)

        self.first_appear_edit = QTimeEdit()
        self.first_appear_edit.setDisplayFormat("HH:mm:ss.zzz")
        self.first_appear_edit.setTime(seconds_to_qtime(self._default_first_appearance_seconds))
        self.first_appear_edit.setFixedWidth(140)
        self.first_appear_edit.timeChanged.connect(self._save_current_entry)

        self.first_duration_spin = QDoubleSpinBox()
        self.first_duration_spin.setRange(0.0, 36000.0)
        self.first_duration_spin.setDecimals(2)
        self.first_duration_spin.setSingleStep(0.5)
        self.first_duration_spin.setValue(self._default_first_duration_seconds)
        self.first_duration_spin.setFixedWidth(104)
        self.first_duration_spin.valueChanged.connect(self._save_current_entry)

        self.second_offset_spin = QDoubleSpinBox()
        self.second_offset_spin.setRange(0.0, 36000.0)
        self.second_offset_spin.setDecimals(2)
        self.second_offset_spin.setSingleStep(0.5)
        self.second_offset_spin.setValue(self._default_second_offset_seconds)
        self.second_offset_spin.setFixedWidth(104)
        self.second_offset_spin.valueChanged.connect(self._save_current_entry)

        self.second_duration_spin = QDoubleSpinBox()
        self.second_duration_spin.setRange(0.0, 36000.0)
        self.second_duration_spin.setDecimals(2)
        self.second_duration_spin.setSingleStep(0.5)
        self.second_duration_spin.setValue(self._default_second_duration_seconds)
        self.second_duration_spin.setFixedWidth(104)
        self.second_duration_spin.valueChanged.connect(self._save_current_entry)

        self.fade_in_spin = QDoubleSpinBox()
        self.fade_in_spin.setFixedWidth(104)
        self.fade_in_spin.setRange(0.0, 10.0)
        self.fade_in_spin.setDecimals(2)
        self.fade_in_spin.setSingleStep(0.1)
        self.fade_in_spin.setValue(0.5)
        self.fade_in_spin.valueChanged.connect(self._on_render_settings_changed)

        self.fade_out_spin = QDoubleSpinBox()
        self.fade_out_spin.setFixedWidth(104)
        self.fade_out_spin.setRange(0.0, 10.0)
        self.fade_out_spin.setDecimals(2)
        self.fade_out_spin.setSingleStep(0.1)
        self.fade_out_spin.setValue(0.5)
        self.fade_out_spin.valueChanged.connect(self._on_render_settings_changed)

        self.font_family_combo = QFontComboBox()
        self.font_family_combo.setMinimumWidth(220)
        self.font_family_combo.setMaximumWidth(260)
        self.font_family_combo.setCurrentFont(QFont("Segoe UI"))
        self.font_family_combo.currentFontChanged.connect(lambda _font: self._on_render_settings_changed())

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 300)
        self.font_size_spin.setValue(48)
        self.font_size_spin.setFixedWidth(68)
        self.font_size_spin.valueChanged.connect(self._on_render_settings_changed)

        self.font_color_button = QPushButton()
        self.font_color_button.setFixedWidth(88)
        self.font_color_button.clicked.connect(self._choose_font_color)

        self.bold_button = self._create_format_button("B", checked=True)
        self.italic_button = self._create_format_button("I")
        self.underline_button = self._create_format_button("U")

        self.outline_spin = QSpinBox()
        self.outline_spin.setRange(0, 12)
        self.outline_spin.setValue(2)
        self.outline_spin.setFixedWidth(68)
        self.outline_spin.valueChanged.connect(self._on_render_settings_changed)

        self.position_x_spin = QSpinBox()
        self.position_x_spin.setRange(0, 10000)
        self.position_x_spin.setFixedWidth(92)
        self.position_x_spin.valueChanged.connect(self._on_position_spin_changed)

        self.position_y_spin = QSpinBox()
        self.position_y_spin.setRange(0, 10000)
        self.position_y_spin.setFixedWidth(92)
        self.position_y_spin.valueChanged.connect(self._on_position_spin_changed)

        self.export_btn = QPushButton("Export Video")
        self.export_btn.clicked.connect(self._export_video)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)

        self._build_menu_bar()
        self._update_color_button()
        self._build_ui()
        self._show_default_timing_controls()
        self._update_window_title()
    def _build_menu_bar(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        new_action = QAction("New Project", self)
        new_action.triggered.connect(self._new_project)
        file_menu.addAction(new_action)

        open_action = QAction("Open Project...", self)
        open_action.triggered.connect(self._open_project)
        file_menu.addAction(open_action)

        save_action = QAction("Save Project", self)
        save_action.triggered.connect(self._save_project)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save Project As...", self)
        save_as_action.triggered.connect(self._save_project_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

    def _create_format_button(self, text: str, checked: bool = False) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setCheckable(True)
        button.setChecked(checked)
        button.setFixedSize(28, 28)
        button.toggled.connect(self._on_render_settings_changed)
        return button

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)

        file_box = QGroupBox("Assets and Export")
        file_layout = QFormLayout(file_box)

        video_row = QHBoxLayout()
        video_row.addWidget(self.video_path_edit)
        import_video_btn = QPushButton("Import Video")
        import_video_btn.clicked.connect(self._import_video)
        video_row.addWidget(import_video_btn)

        graphic_row = QHBoxLayout()
        graphic_row.addWidget(self.graphic_path_edit)
        import_graphic_btn = QPushButton("Import Optional PNG")
        import_graphic_btn.clicked.connect(self._import_graphic)
        graphic_row.addWidget(import_graphic_btn)

        export_dir_row = QHBoxLayout()
        export_dir_row.addWidget(self.export_dir_edit)
        browse_export_btn = QPushButton("Browse")
        browse_export_btn.clicked.connect(self._choose_export_directory)
        export_dir_row.addWidget(browse_export_btn)

        file_layout.addRow("Video", self._wrap_layout(video_row))
        file_layout.addRow("Optional PNG Overlay", self._wrap_layout(graphic_row))
        file_layout.addRow("Export Directory", self._wrap_layout(export_dir_row))
        file_layout.addRow("Export Format", self.export_format_combo)
        file_layout.addRow("Export Resolution", self.resolution_combo)
        main_layout.addWidget(file_box)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter, 1)

        export_row = QHBoxLayout()
        export_row.addStretch(1)
        refresh_btn = QPushButton("Refresh Preview")
        refresh_btn.clicked.connect(self.refresh_preview)
        export_row.addWidget(refresh_btn)
        export_row.addWidget(self.export_btn)
        main_layout.addLayout(export_row)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        title = QLabel("Titles")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)
        layout.addWidget(self.entry_list, 1)

        buttons = QHBoxLayout()
        add_btn = QPushButton("Add Title")
        add_btn.clicked.connect(self._add_entry)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_entry)
        buttons.addWidget(add_btn)
        buttons.addWidget(remove_btn)
        layout.addLayout(buttons)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self._build_editor_panel())
        return panel

    def _build_editor_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        editor_box = QGroupBox("Selected Title")
        editor_form = QFormLayout(editor_box)
        editor_form.addRow("Text", self.text_edit)

        timing_box = QGroupBox("Timing")
        timing_form = QFormLayout(timing_box)
        timing_form.setHorizontalSpacing(18)
        timing_form.setVerticalSpacing(10)

        first_row = QHBoxLayout()
        first_row.setSpacing(10)
        first_appear_label = QLabel("Appear At")
        first_appear_label.setFixedWidth(96)
        first_duration_label = QLabel("Duration (s)")
        first_duration_label.setFixedWidth(82)
        first_row.addWidget(first_appear_label)
        first_row.addWidget(self.first_appear_edit)
        first_row.addSpacing(8)
        first_row.addWidget(first_duration_label)
        first_row.addWidget(self.first_duration_spin)
        first_row.addStretch(1)
        timing_form.addRow("First Appearance", self._wrap_layout(first_row))

        second_row = QHBoxLayout()
        second_row.setSpacing(10)
        second_offset_label = QLabel("Offset From End")
        second_offset_label.setFixedWidth(96)
        second_duration_label = QLabel("Duration (s)")
        second_duration_label.setFixedWidth(82)
        second_row.addWidget(second_offset_label)
        second_row.addWidget(self.second_offset_spin)
        second_row.addSpacing(8)
        second_row.addWidget(second_duration_label)
        second_row.addWidget(self.second_duration_spin)
        second_row.addStretch(1)
        timing_form.addRow("Second Appearance", self._wrap_layout(second_row))

        fade_row = QHBoxLayout()
        fade_row.setSpacing(10)
        fade_in_label = QLabel("Fade In (s)")
        fade_in_label.setFixedWidth(96)
        fade_out_label = QLabel("Fade Out (s)")
        fade_out_label.setFixedWidth(82)
        fade_row.addWidget(fade_in_label)
        fade_row.addWidget(self.fade_in_spin)
        fade_row.addSpacing(8)
        fade_row.addWidget(fade_out_label)
        fade_row.addWidget(self.fade_out_spin)
        fade_row.addStretch(1)
        timing_form.addRow("Fade", self._wrap_layout(fade_row))

        style_box = QGroupBox("Text Style")
        style_layout = QVBoxLayout(style_box)
        typography_row = QHBoxLayout()
        typography_row.addWidget(self.font_family_combo, 1)
        typography_row.addWidget(self.font_size_spin)
        typography_row.addWidget(self.bold_button)
        typography_row.addWidget(self.italic_button)
        typography_row.addWidget(self.underline_button)
        typography_row.addWidget(QLabel("Outline"))
        typography_row.addWidget(self.outline_spin)
        typography_row.addWidget(self.font_color_button)
        style_layout.addLayout(typography_row)

        position_box = QGroupBox("Position")
        position_form = QFormLayout(position_box)
        position_form.setHorizontalSpacing(18)
        position_form.setVerticalSpacing(8)
        position_hint = QLabel(
            "Drag the text directly in the preview to place it. X and Y remain available as secondary values."
        )
        position_hint.setWordWrap(True)
        position_form.addRow(position_hint)

        position_row = QHBoxLayout()
        position_row.setSpacing(10)
        position_x_label = QLabel("X")
        position_x_label.setFixedWidth(12)
        position_y_label = QLabel("Y")
        position_y_label.setFixedWidth(12)
        position_row.addWidget(position_x_label)
        position_row.addWidget(self.position_x_spin)
        position_row.addSpacing(12)
        position_row.addWidget(position_y_label)
        position_row.addWidget(self.position_y_spin)
        position_row.addStretch(1)
        position_form.addRow("Position", self._wrap_layout(position_row))

        layout.addWidget(editor_box)
        layout.addWidget(timing_box)
        layout.addWidget(style_box)
        layout.addWidget(position_box)
        return panel

    @staticmethod
    def _wrap_layout(layout: QHBoxLayout) -> QWidget:
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def _populate_resolution_options(self) -> None:
        self.resolution_combo.clear()
        self.resolution_combo.addItem("Source / Original", None)
        self.resolution_combo.addItem("720x576", (720, 576))
        self.resolution_combo.addItem("1280x720", (1280, 720))
        self.resolution_combo.addItem("1920x1080", (1920, 1080))

    def _populate_export_format_options(self) -> None:
        self.export_format_combo.clear()
        self.export_format_combo.addItem("MP4 (H.264)", "mp4_h264")
        self.export_format_combo.addItem("MPEG-2 Program Stream (.mpg)", "mpeg2_ps")
        self.export_format_combo.addItem("MPEG-4 (.mp4)", "mpeg4")

    def _selected_export_format(self) -> str:
        data = self.export_format_combo.currentData()
        return str(data or "mp4_h264")

    def _update_window_title(self) -> None:
        project_name = self._project_path.name if self._project_path else "Untitled.brnr"
        dirty_prefix = "*" if self._project_dirty else ""
        self.setWindowTitle(f"{dirty_prefix}{project_name} - Lower Third Burner")

    def _set_project_dirty(self, is_dirty: bool = True) -> None:
        if self._loading_project:
            return
        self._project_dirty = is_dirty
        self._update_window_title()

    def _project_payload(self) -> dict[str, object]:
        self._save_current_entry()
        resolution = self._selected_output_resolution()
        return {
            "version": 1,
            "video_path": self.video_path,
            "graphic_path": self.graphic_path,
            "export_directory": self.export_dir_edit.text().strip() or None,
            "export_format": self._selected_export_format(),
            "resolution": list(resolution) if resolution else None,
            "font_family": self.font_family_combo.currentFont().family(),
            "font_size": self.font_size_spin.value(),
            "font_color": self._font_color,
            "bold": self.bold_button.isChecked(),
            "italic": self.italic_button.isChecked(),
            "underline": self.underline_button.isChecked(),
            "outline_size": self.outline_spin.value(),
            "text_x": self.position_x_spin.value(),
            "text_y": self.position_y_spin.value(),
            "fade_in_seconds": self.fade_in_spin.value(),
            "fade_out_seconds": self.fade_out_spin.value(),
            "entries": [
                {
                    "text": entry.text,
                    "first_start_time_seconds": entry.first_start_time_seconds,
                    "first_duration_seconds": entry.first_duration_seconds,
                    "second_end_offset_seconds": entry.second_end_offset_seconds,
                    "second_duration_seconds": entry.second_duration_seconds,
                }
                for entry in self.entries
            ],
        }
    def _apply_project_payload(self, payload: dict[str, object], project_path: Path | None) -> None:
        self._loading_project = True
        try:
            self._reset_editor_state()
            self._project_path = project_path

            video_path = payload.get("video_path")
            if isinstance(video_path, str) and video_path:
                self._load_video_from_path(video_path, show_message=False)

            graphic_path = payload.get("graphic_path")
            if isinstance(graphic_path, str) and graphic_path:
                self.graphic_path = graphic_path
                self.graphic_path_edit.setText(graphic_path)

            export_directory = payload.get("export_directory")
            self.export_dir_edit.setText(export_directory if isinstance(export_directory, str) else "")

            export_format = str(payload.get("export_format") or "mp4_h264")
            export_format_index = self.export_format_combo.findData(export_format)
            self.export_format_combo.setCurrentIndex(max(0, export_format_index))

            resolution = payload.get("resolution")
            if isinstance(resolution, list) and len(resolution) == 2:
                resolution_index = self.resolution_combo.findData((int(resolution[0]), int(resolution[1])))
                self.resolution_combo.setCurrentIndex(max(0, resolution_index))
            else:
                self.resolution_combo.setCurrentIndex(0)

            font_family = str(payload.get("font_family") or "Segoe UI")
            self.font_family_combo.setCurrentFont(QFont(font_family))
            self.font_size_spin.setValue(int(payload.get("font_size") or 48))
            self._font_color = str(payload.get("font_color") or "#FFFFFF")
            self._update_color_button()
            self.bold_button.setChecked(bool(payload.get("bold", True)))
            self.italic_button.setChecked(bool(payload.get("italic", False)))
            self.underline_button.setChecked(bool(payload.get("underline", False)))
            self.outline_spin.setValue(int(payload.get("outline_size") or 2))
            self.position_x_spin.setValue(int(payload.get("text_x") or 0))
            self.position_y_spin.setValue(int(payload.get("text_y") or 0))
            self.fade_in_spin.setValue(float(payload.get("fade_in_seconds") or 0.5))
            self.fade_out_spin.setValue(float(payload.get("fade_out_seconds") or 0.5))

            loaded_entries: list[TitleEntry] = []
            raw_entries = payload.get("entries")
            if isinstance(raw_entries, list):
                for item in raw_entries:
                    if not isinstance(item, dict):
                        continue
                    loaded_entries.append(
                        TitleEntry(
                            text=str(item.get("text") or ""),
                            first_start_time_seconds=float(item.get("first_start_time_seconds") or 20.0),
                            first_duration_seconds=float(item.get("first_duration_seconds") or 5.0),
                            second_end_offset_seconds=float(item.get("second_end_offset_seconds") or 10.0),
                            second_duration_seconds=float(item.get("second_duration_seconds") or 5.0),
                        )
                    )
            self.entries = loaded_entries
            self.entry_list.clear()
            for index, entry in enumerate(self.entries, start=1):
                self.entry_list.addItem(self._entry_label(entry, index))
            if self.entries:
                self.entry_list.setCurrentRow(0)
            else:
                self._show_default_timing_controls()

            self._update_position_ranges()
            self.refresh_preview()
            self._set_project_dirty(False)
        finally:
            self._loading_project = False
            self._update_window_title()

    def _reset_editor_state(self) -> None:
        self.video_path = None
        self.graphic_path = None
        self.video_metadata = None
        self.preview_frame_path = None
        self.entries = []
        self.video_path_edit.clear()
        self.graphic_path_edit.clear()
        self.export_dir_edit.clear()
        self.entry_list.clear()
        self.text_edit.clear()
        self.preview.update_preview(None, None, None, None, self._current_render_settings())

    def _confirm_discard_changes(self) -> bool:
        if not self._project_dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved Changes",
            "This project has unsaved changes. Do you want to save them first?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Save:
            return self._save_project()
        if answer == QMessageBox.Discard:
            return True
        return False

    def _new_project(self) -> None:
        if not self._confirm_discard_changes():
            return
        self._loading_project = True
        try:
            self._project_path = None
            self._reset_editor_state()
            self._show_default_timing_controls()
        finally:
            self._loading_project = False
        self._set_project_dirty(False)
        self.status_bar.showMessage("Started a new project.", 3000)

    def _open_project(self) -> None:
        if not self._confirm_discard_changes():
            return
        start_dir = str(self._project_path.parent) if self._project_path else str(Path.home())
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Project", start_dir, "Burner Project (*.brnr)")
        if not file_path:
            return
        try:
            payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise FFmpegError("The selected project file is invalid.")
            self._apply_project_payload(payload, Path(file_path))
            self.status_bar.showMessage(f"Opened project: {file_path}", 4000)
        except Exception as exc:
            self._show_error(f"Could not open the selected project file.\n\n{exc}")

    def _write_project_file(self, path: Path) -> bool:
        try:
            path.write_text(json.dumps(self._project_payload(), indent=2), encoding="utf-8")
        except Exception as exc:
            self._show_error(f"Could not save the project file.\n\n{exc}")
            return False
        self._project_path = path
        self._set_project_dirty(False)
        self.status_bar.showMessage(f"Saved project: {path}", 4000)
        return True

    def _save_project(self) -> bool:
        if self._project_path is None:
            return self._save_project_as()
        return self._write_project_file(self._project_path)

    def _save_project_as(self) -> bool:
        start_dir = str(self._project_path.parent) if self._project_path else str(Path.home())
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Project As", start_dir, "Burner Project (*.brnr)")
        if not file_path:
            return False
        path = Path(file_path)
        if path.suffix.lower() != ".brnr":
            path = path.with_suffix(".brnr")
        return self._write_project_file(path)

    def _show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            "About Lower Third Burner",
            "Lower Third Burner\n\n"
            "A desktop tool for importing video, adding lower-third graphics and text, and exporting a rendered video file.\n\n"
            "Created by Valentin Kirov\n\n"
            "License: Apache-2.0\n"
            "Licensed under the Apache License 2.0. Attribution and notices must be preserved.",
        )

    def _load_video_from_path(self, file_path: str, show_message: bool = True) -> None:
        metadata_payload = get_video_metadata(file_path)
        metadata = VideoMetadata(
            width=int(metadata_payload["width"]),
            height=int(metadata_payload["height"]),
            duration_seconds=float(metadata_payload["duration"]),
        )
        self.settings_store.setValue("video_import_directory", str(Path(file_path).parent))
        self.video_path = file_path
        self.video_metadata = metadata
        self.video_path_edit.setText(file_path)
        self._update_resolution_options()
        self._set_default_entry_times()
        self._set_default_text_position()
        if show_message:
            self.status_bar.showMessage(
                f"Loaded video: {metadata.width}x{metadata.height}, duration {metadata.duration_seconds:.2f}s",
                5000,
            )
        self.refresh_preview()

    def _update_resolution_options(self) -> None:
        label = "Source / Original"
        if self.video_metadata:
            label = f"Source / Original ({self.video_metadata.width}x{self.video_metadata.height})"
        self.resolution_combo.setItemText(0, label)

    def _choose_export_directory(self) -> None:
        start_dir = self.export_dir_edit.text().strip()
        if not start_dir and self.video_path:
            start_dir = str(Path(self.video_path).parent)
        directory = QFileDialog.getExistingDirectory(self, "Select Export Directory", start_dir)
        if not directory:
            return
        self.export_dir_edit.setText(directory)
        self._set_project_dirty(True)

    def _choose_font_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._font_color), self, "Choose Text Color")
        if not color.isValid():
            return
        self._font_color = color.name().upper()
        self._update_color_button()
        self._update_preview_overlay()
        self._set_project_dirty(True)

    def _update_color_button(self) -> None:
        button_text_color = "#000000" if QColor(self._font_color).lightness() > 128 else "#FFFFFF"
        self.font_color_button.setText(self._font_color)
        self.font_color_button.setStyleSheet(
            f"background-color: {self._font_color}; color: {button_text_color}; font-weight: 600;"
        )

    def _selected_output_resolution(self) -> tuple[int, int] | None:
        data = self.resolution_combo.currentData()
        return data if isinstance(data, tuple) else None

    def _preview_metadata(self) -> VideoMetadata | None:
        if not self.video_metadata:
            return None
        resolution = self._selected_output_resolution()
        if not resolution:
            return self.video_metadata
        return VideoMetadata(width=resolution[0], height=resolution[1], duration_seconds=self.video_metadata.duration_seconds)

    def _current_render_settings(self) -> RenderSettings:
        resolution = self._selected_output_resolution()
        return RenderSettings(
            output_width=resolution[0] if resolution else None,
            output_height=resolution[1] if resolution else None,
            text_x=self.position_x_spin.value(),
            text_y=self.position_y_spin.value(),
            font_family=self.font_family_combo.currentFont().family(),
            font_size=self.font_size_spin.value(),
            font_color=self._font_color,
            bold=self.bold_button.isChecked(),
            italic=self.italic_button.isChecked(),
            underline=self.underline_button.isChecked(),
            outline_size=self.outline_spin.value(),
            fade_in_seconds=self.fade_in_spin.value(),
            fade_out_seconds=self.fade_out_spin.value(),
            export_directory=self.export_dir_edit.text().strip() or None,
            export_format=self._selected_export_format(),
        )
    def _default_first_timing(self, duration_seconds: float | None) -> tuple[float, float]:
        if not duration_seconds or duration_seconds <= 0:
            return 20.0, 5.0
        duration = min(5.0, duration_seconds)
        appear_at = min(20.0, max(0.0, duration_seconds - duration))
        return appear_at, duration

    def _default_second_timing(self, duration_seconds: float | None) -> tuple[float, float]:
        if not duration_seconds or duration_seconds <= 0:
            return 10.0, 5.0
        duration = min(5.0, duration_seconds)
        offset = min(10.0, duration_seconds)
        return offset, duration

    def _set_default_entry_times(self) -> None:
        duration = self.video_metadata.duration_seconds if self.video_metadata else None
        self._default_first_appearance_seconds, self._default_first_duration_seconds = self._default_first_timing(duration)
        self._default_second_offset_seconds, self._default_second_duration_seconds = self._default_second_timing(duration)
        if self.entry_list.currentRow() < 0:
            self._show_default_timing_controls()

    def _show_default_timing_controls(self) -> None:
        self._loading_entry = True
        self.first_appear_edit.setTime(seconds_to_qtime(self._default_first_appearance_seconds))
        self.first_duration_spin.setValue(self._default_first_duration_seconds)
        self.second_offset_spin.setValue(self._default_second_offset_seconds)
        self.second_duration_spin.setValue(self._default_second_duration_seconds)
        self._loading_entry = False

    def _set_default_text_position(self) -> None:
        metadata = self._preview_metadata()
        if not metadata:
            return
        self._syncing_preview_position = True
        self.position_x_spin.setValue(max(24, metadata.width // 10))
        self.position_y_spin.setValue(max(24, metadata.height - 120))
        self.font_size_spin.setValue(max(28, metadata.height // 24))
        self._syncing_preview_position = False
        self._update_position_ranges()

    def _update_position_ranges(self) -> None:
        metadata = self._preview_metadata()
        if not metadata:
            self.position_x_spin.setRange(0, 10000)
            self.position_y_spin.setRange(0, 10000)
            return
        self.position_x_spin.setRange(0, metadata.width)
        self.position_y_spin.setRange(0, metadata.height)
        self._syncing_preview_position = True
        self.position_x_spin.setValue(min(self.position_x_spin.value(), metadata.width))
        self.position_y_spin.setValue(min(self.position_y_spin.value(), metadata.height))
        self._syncing_preview_position = False

    def _on_resolution_changed(self) -> None:
        self._update_position_ranges()
        self._update_preview_overlay()
        self._set_project_dirty(True)

    def _on_render_settings_changed(self) -> None:
        self._update_preview_overlay()
        self._set_project_dirty(True)

    def _on_position_spin_changed(self) -> None:
        if self._syncing_preview_position:
            return
        self._update_preview_overlay()
        self._set_project_dirty(True)

    def _on_preview_text_dragged(self, x: int, y: int) -> None:
        self._syncing_preview_position = True
        self.position_x_spin.setValue(x)
        self.position_y_spin.setValue(y)
        self._syncing_preview_position = False
        self._set_project_dirty(True)

    def _last_video_import_directory(self) -> str:
        stored = self.settings_store.value("video_import_directory", "", type=str)
        if stored and Path(stored).exists():
            return stored
        if self.video_path:
            return str(Path(self.video_path).parent)
        return str(Path.home())

    def _import_video(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video",
            self._last_video_import_directory(),
            "Video Files (*.mp4 *.mov *.avi *.mkv *.mpg *.mpeg *.m4v *.wmv *.ts *.m2ts *.webm *.mxf);;All Files (*.*)",
        )
        if not file_path:
            return
        try:
            self._load_video_from_path(file_path)
            self._set_project_dirty(True)
        except FFmpegError as exc:
            self._show_error(str(exc))
        except Exception as exc:
            self._show_error(f"Could not import the selected video file.\n\n{exc}")

    def _import_graphic(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Transparent PNG", "", "PNG Files (*.png)")
        if not file_path:
            return
        self.graphic_path = file_path
        self.graphic_path_edit.setText(file_path)
        self.status_bar.showMessage("Optional PNG overlay loaded.", 4000)
        self._update_preview_overlay()
        self._set_project_dirty(True)

    def _new_entry_defaults(self) -> tuple[float, float, float, float]:
        return (
            self._default_first_appearance_seconds,
            self._default_first_duration_seconds,
            self._default_second_offset_seconds,
            self._default_second_duration_seconds,
        )

    def _add_entry(self) -> None:
        first_start, first_duration, second_offset, second_duration = self._new_entry_defaults()
        entry = TitleEntry(
            text="New Title",
            first_start_time_seconds=first_start,
            first_duration_seconds=first_duration,
            second_end_offset_seconds=second_offset,
            second_duration_seconds=second_duration,
        )
        self.entries.append(entry)
        self.entry_list.addItem(self._entry_label(entry, len(self.entries)))
        self.entry_list.setCurrentRow(len(self.entries) - 1)
        self.status_bar.showMessage("Added a lower-third title entry.", 3000)
        self._set_project_dirty(True)

    def _remove_entry(self) -> None:
        row = self.entry_list.currentRow()
        if row < 0:
            return
        del self.entries[row]
        self.entry_list.takeItem(row)
        if self.entries:
            self.entry_list.setCurrentRow(min(row, len(self.entries) - 1))
        else:
            self._loading_entry = True
            self.text_edit.clear()
            self._show_default_timing_controls()
            self._loading_entry = False
            self._update_preview_overlay()
        self._refresh_entry_labels()
        self._set_project_dirty(True)

    def _populate_timing_controls(self, entry: TitleEntry) -> None:
        self._loading_entry = True
        self.first_appear_edit.setTime(seconds_to_qtime(entry.first_start_time_seconds))
        self.first_duration_spin.setValue(entry.first_duration_seconds)
        self.second_offset_spin.setValue(entry.second_end_offset_seconds)
        self.second_duration_spin.setValue(entry.second_duration_seconds)
        self._loading_entry = False

    def _on_entry_selected(self, row: int) -> None:
        self._loading_entry = True
        if row < 0 or row >= len(self.entries):
            self.text_edit.clear()
            self._show_default_timing_controls()
            self._loading_entry = False
            self._update_preview_overlay()
            return
        entry = self.entries[row]
        self.text_edit.setPlainText(entry.text)
        self._populate_timing_controls(entry)
        self._loading_entry = False
        self._update_preview_overlay()

    def _save_current_entry(self) -> None:
        if self._loading_entry:
            return
        row = self.entry_list.currentRow()
        if row < 0 or row >= len(self.entries):
            return
        entry = self.entries[row]
        entry.text = self.text_edit.toPlainText().strip()
        entry.first_start_time_seconds = qtime_to_seconds(self.first_appear_edit.time())
        entry.first_duration_seconds = self.first_duration_spin.value()
        entry.second_end_offset_seconds = self.second_offset_spin.value()
        entry.second_duration_seconds = self.second_duration_spin.value()
        self.entry_list.item(row).setText(self._entry_label(entry, row + 1))
        self._update_preview_overlay()
        self._set_project_dirty(True)

    def _entry_label(self, entry: TitleEntry, index: int) -> str:
        preview_text = entry.text.replace("\n", " ").strip() or "Untitled"
        preview_text = preview_text[:32]
        if self.video_metadata:
            appearances = entry.resolved_appearances(self.video_metadata.duration_seconds)
            windows = ", ".join(f"{start:.2f}s-{end:.2f}s" for start, end in appearances) or "No visible time"
            return f"{index}. {preview_text} [{windows}]"
        return (
            f"{index}. {preview_text} "
            f"[First: {entry.first_start_time_seconds:.2f}s/{entry.first_duration_seconds:.2f}s, "
            f"Second: -{entry.second_end_offset_seconds:.2f}s/{entry.second_duration_seconds:.2f}s]"
        )

    def _refresh_entry_labels(self) -> None:
        for idx, entry in enumerate(self.entries):
            item = self.entry_list.item(idx)
            if item:
                item.setText(self._entry_label(entry, idx + 1))

    def _selected_entry(self) -> TitleEntry | None:
        row = self.entry_list.currentRow()
        if 0 <= row < len(self.entries):
            return self.entries[row]
        return None

    def _update_preview_overlay(self) -> None:
        self.preview.update_preview(
            self.preview_frame_path,
            self.graphic_path,
            self._preview_metadata(),
            self._selected_entry(),
            self._current_render_settings(),
        )

    def refresh_preview(self) -> None:
        selected_entry = self._selected_entry()
        preview_timestamp = self._default_first_appearance_seconds
        if selected_entry and self.video_metadata:
            appearances = selected_entry.resolved_appearances(self.video_metadata.duration_seconds)
            if appearances:
                preview_timestamp = appearances[0][0]
        if self.video_path and self.video_metadata:
            try:
                output_path = temporary_preview_image()
                extract_preview_frame(self.video_path, output_path, preview_timestamp)
                self.preview_frame_path = output_path
            except FFmpegError as exc:
                self.preview_frame_path = None
                self.status_bar.showMessage(f"Preview frame failed: {exc}", 6000)
        self._update_preview_overlay()
    def _resolve_export_directory(self) -> Path | None:
        configured = self.export_dir_edit.text().strip()
        if configured:
            path = Path(configured)
        elif self.video_path:
            path = Path(self.video_path).parent
        else:
            return None
        if not path.exists() or not path.is_dir():
            self._show_error("Choose a valid export directory.")
            return None
        return path

    def _build_output_path(self) -> Path | None:
        export_directory = self._resolve_export_directory()
        if not export_directory or not self.video_path:
            return None
        extension = output_extension_for_format(self._selected_export_format())
        return export_directory / f"{Path(self.video_path).stem}_lower_thirds{extension}"

    def _validate_entries(self) -> bool:
        if not self.video_path:
            self._show_error("Import a video file first.")
            return False
        if not self.entries:
            self._show_error("Add at least one title entry before exporting.")
            return False
        if not self.video_metadata:
            self._show_error("Video metadata is not available.")
            return False
        try:
            find_font_path(
                self.font_family_combo.currentFont().family(),
                self.bold_button.isChecked(),
                self.italic_button.isChecked(),
            )
        except FFmpegError as exc:
            self._show_error(str(exc))
            return False
        try:
            validate_export_settings(self._current_render_settings(), self.video_metadata, 25.0)
        except FFmpegError as exc:
            self._show_error(str(exc))
            return False
        for idx, entry in enumerate(self.entries, start=1):
            if not entry.text.strip():
                self._show_error(f"Title {idx} has no text.")
                return False
            appearances = entry.resolved_appearances(self.video_metadata.duration_seconds)
            if not appearances:
                self._show_error(f"Title {idx} has no visible time inside the video.")
                return False
            for start_time, end_time in appearances:
                if end_time <= start_time:
                    self._show_error(f"Title {idx} has an invalid timing range.")
                    return False
        return self._build_output_path() is not None

    def _set_export_running(self, is_running: bool) -> None:
        self.export_btn.setEnabled(not is_running)
        self.progress_bar.setVisible(is_running)
        if not is_running:
            self.progress_bar.setValue(0)

    def _on_export_progress(self, value: int) -> None:
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(value)
        self.status_bar.showMessage(f"Rendering video... {value}%")

    def _on_export_success(self, output_path: str) -> None:
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(100)
        self.status_bar.showMessage("Export complete.", 5000)
        QMessageBox.information(self, "Export Complete", f"Saved rendered video to:\n{output_path}")
        self._set_export_running(False)

    def _on_export_error(self, message: str) -> None:
        self._set_export_running(False)
        self._show_error(message or "Export failed.")

    def _on_export_thread_finished(self) -> None:
        self._export_worker = None
        self._export_thread = None

    def _export_video(self) -> None:
        self._save_current_entry()
        if self._export_thread is not None:
            self._show_error("An export is already running.")
            return
        if not self._validate_entries():
            return
        output_path = self._build_output_path()
        if not output_path or not self.video_path:
            return
        if output_path.exists():
            answer = QMessageBox.question(
                self,
                "Overwrite File",
                f"The file already exists:\n{output_path}\n\nDo you want to overwrite it?",
            )
            if answer != QMessageBox.Yes:
                return
        settings = self._current_render_settings()
        entries = [
            TitleEntry(
                text=entry.text,
                first_start_time_seconds=entry.first_start_time_seconds,
                first_duration_seconds=entry.first_duration_seconds,
                second_end_offset_seconds=entry.second_end_offset_seconds,
                second_duration_seconds=entry.second_duration_seconds,
            )
            for entry in self.entries
        ]
        self._set_export_running(True)
        self.progress_bar.setValue(0)
        self.status_bar.showMessage("Rendering video...")
        thread = QThread(self)
        worker = ExportWorker(self.video_path, self.graphic_path, entries, str(output_path), settings)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress_changed.connect(self._on_export_progress)
        worker.completed.connect(self._on_export_success)
        worker.failed.connect(self._on_export_error)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.completed.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_export_thread_finished)
        self._export_thread = thread
        self._export_worker = worker
        thread.start()

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Lower Third Burner", message)
        self.status_bar.showMessage(message, 6000)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._export_thread is not None:
            self._show_error("Wait for the current export to finish before closing the app.")
            event.ignore()
            return
        if not self._confirm_discard_changes():
            event.ignore()
            return
        event.accept()


def run() -> None:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Lower Third Burner")
    app.setOrganizationName("Valentin Kirov")
    app.setOrganizationDomain("lower-third-burner.local")
    window = MainWindow()
    window.show()
    app.exec()
