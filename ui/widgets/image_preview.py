"""Zoomable image preview with drag-and-drop support."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QWidget

# 超过约 3000 万像素的图先降采样到 6000px 内再进预览，避免 9000px 级
# 印刷大图整幅解码导致切页卡顿；预览质量不受影响（屏幕远小于 6000px）。
_MAX_PREVIEW_PIXELS = 30_000_000
_SCALED_LONG_EDGE = 6000

_MIN_ZOOM = 0.02
_MAX_ZOOM = 12.0


class ImagePreview(QGraphicsView):
    pathDropped = Signal(str)

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item: "QGraphicsPixmapItem | None" = None
        self.zoom_level = 1.0
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHints(
            QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing
        )
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumSize(QSize(360, 300))

    def load_image(self, path: Path) -> bool:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.width() * size.height() > _MAX_PREVIEW_PIXELS:
            reader.setScaledSize(
                size.scaled(_SCALED_LONG_EDGE, _SCALED_LONG_EDGE, Qt.AspectRatioMode.KeepAspectRatio)
            )
        image = reader.read()
        if image.isNull():
            return False
        pixmap = QPixmap.fromImage(image)
        self.scene.clear()
        self.pixmap_item = QGraphicsPixmapItem(pixmap)
        self.pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene.addItem(self.pixmap_item)
        self.scene.setSceneRect(self.pixmap_item.boundingRect())
        self.zoom_level = 1.0
        QTimer.singleShot(0, self.fit_image)
        return True

    def clear_image(self) -> None:
        self.scene.clear()
        self.pixmap_item = None
        self.zoom_level = 1.0
        self.resetTransform()

    def fit_image(self) -> None:
        if self.pixmap_item is None:
            return
        self.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self.zoom_level = 1.0

    def actual_size(self) -> None:
        if self.pixmap_item is None:
            return
        self.resetTransform()
        self.zoom_level = 1.0

    def zoom(self, factor: float) -> None:
        if self.pixmap_item is None:
            return
        target = self.zoom_level * factor
        clamped = max(_MIN_ZOOM, min(_MAX_ZOOM, target))
        if clamped == self.zoom_level:
            return
        factor = clamped / self.zoom_level
        self.zoom_level = clamped
        self.scale(factor, factor)

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.pixmap_item is None:
            super().wheelEvent(event)
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.zoom(factor)

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path:
            self.pathDropped.emit(path)
            event.acceptProposedAction()
