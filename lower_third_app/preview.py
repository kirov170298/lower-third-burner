from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView

from .models import RenderSettings, TitleEntry, VideoMetadata


class DraggableTextItem(QGraphicsTextItem):
    def __init__(self, text: str, position_callback) -> None:
        super().__init__(text)
        self._position_callback = position_callback
        self.setFlag(QGraphicsTextItem.ItemIsMovable, True)
        self.setFlag(QGraphicsTextItem.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsTextItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.OpenHandCursor)

    def mousePressEvent(self, event) -> None:
        self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsTextItem.ItemPositionHasChanged and self._position_callback:
            self._position_callback(value)
        return super().itemChange(change, value)


class PreviewView(QGraphicsView):
    text_position_changed = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(720, 420)
        self._placeholder_message = "Import a video to preview the lower-third placement."
        self.clear_preview()

    def clear_preview(self) -> None:
        self._scene.clear()
        self._scene.setSceneRect(QRectF(0, 0, 1280, 720))
        self._scene.setBackgroundBrush(QColor("#202020"))
        message = self._scene.addText(self._placeholder_message, QFont("Segoe UI", 16))
        message.setDefaultTextColor(QColor("white"))
        message.setPos(90, 330)
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    def _emit_dragged_position(self, value) -> None:
        x = max(0, int(round(value.x())))
        y = max(0, int(round(value.y())))
        self.text_position_changed.emit(x, y)

    def update_preview(
        self,
        frame_path: str | None,
        graphic_path: str | None,
        metadata: VideoMetadata | None,
        entry: TitleEntry | None,
        settings: RenderSettings | None,
    ) -> None:
        if not metadata:
            self.clear_preview()
            return

        self._scene.clear()
        self._scene.setSceneRect(QRectF(0, 0, metadata.width, metadata.height))

        frame_pixmap = QPixmap(frame_path) if frame_path and Path(frame_path).exists() else QPixmap()
        if frame_pixmap.isNull():
            frame_pixmap = QPixmap(metadata.width, metadata.height)
            frame_pixmap.fill(QColor("#111111"))
        elif frame_pixmap.size().width() != metadata.width or frame_pixmap.size().height() != metadata.height:
            frame_pixmap = frame_pixmap.scaled(metadata.width, metadata.height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self._scene.addItem(QGraphicsPixmapItem(frame_pixmap))

        if graphic_path and Path(graphic_path).exists():
            graphic = QPixmap(graphic_path)
            if not graphic.isNull():
                item = QGraphicsPixmapItem(graphic)
                x_scale = metadata.width / max(1, frame_pixmap.width())
                y_scale = metadata.height / max(1, frame_pixmap.height())
                if x_scale != 1.0 or y_scale != 1.0:
                    item.setScale(min(x_scale, y_scale))
                x = (metadata.width - item.boundingRect().width() * item.scale()) / 2
                y = metadata.height - item.boundingRect().height() * item.scale() - max(12, round(metadata.height * 0.05))
                item.setPos(x, y)
                self._scene.addItem(item)

        if entry and entry.text.strip() and settings:
            text_item = DraggableTextItem(entry.text, self._emit_dragged_position)
            font = QFont(settings.font_family, settings.font_size)
            font.setBold(settings.bold)
            font.setItalic(settings.italic)
            font.setUnderline(settings.underline)
            text_item.setFont(font)
            text_item.setDefaultTextColor(QColor(settings.font_color))
            text_item.setPos(settings.text_x, settings.text_y)
            self._scene.addItem(text_item)

        guide_pen = QPen(QColor(255, 255, 255, 80))
        guide_pen.setStyle(Qt.DashLine)
        self._scene.addRect(QRectF(24, 24, metadata.width - 48, metadata.height - 48), guide_pen)
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
