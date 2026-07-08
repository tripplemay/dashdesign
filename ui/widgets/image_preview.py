"""Zoomable image preview with drag-and-drop support."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImageReader, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

# 超过约 3000 万像素的图先降采样到 6000px 内再进预览，避免 9000px 级
# 印刷大图整幅解码导致切页卡顿；预览质量不受影响（屏幕远小于 6000px）。
_MAX_PREVIEW_PIXELS = 30_000_000
_SCALED_LONG_EDGE = 6000

_MIN_ZOOM = 0.02
_MAX_ZOOM = 12.0


class ImagePreview(QGraphicsView):
    pathDropped = Signal(str)
    # 框选完成信号：矩形坐标基于原图像素（预览可能是降采样图，已换算回原尺寸）。
    selectionMade = Signal(QRect, QSize)

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item: "QGraphicsPixmapItem | None" = None
        self.zoom_level = 1.0
        self._source_size = QSize()
        self._selection_mode = False
        self._selection_item: "QGraphicsRectItem | None" = None
        self._selection_origin = None
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
        self._selection_item = None
        self._selection_origin = None
        self._source_size = size if size.isValid() else pixmap.size()
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
        self._selection_item = None
        self._selection_origin = None
        self._source_size = QSize()
        self.zoom_level = 1.0
        self.resetTransform()

    def source_size(self) -> QSize:
        return QSize(self._source_size)

    def set_selection_mode(self, enabled: bool) -> None:
        self._selection_mode = enabled
        if enabled:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.viewport().unsetCursor()
            # 保留已画的框，让用户对照坐标；下次加载图片时才清除。

    def clear_selection(self) -> None:
        if self._selection_item is not None and self._selection_item.scene() is not None:
            self.scene.removeItem(self._selection_item)
        self._selection_item = None
        self._selection_origin = None

    def _ensure_selection_item(self) -> QGraphicsRectItem:
        if self._selection_item is None or self._selection_item.scene() is None:
            item = QGraphicsRectItem()
            pen = QPen(QColor(224, 62, 62), 0)
            pen.setStyle(Qt.PenStyle.DashLine)
            item.setPen(pen)
            item.setBrush(QBrush(QColor(224, 62, 62, 60)))
            item.setZValue(10)
            self.scene.addItem(item)
            self._selection_item = item
        return self._selection_item

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._selection_mode and self.pixmap_item is not None and event.button() == Qt.MouseButton.LeftButton:
            self._selection_origin = self.mapToScene(event.position().toPoint())
            item = self._ensure_selection_item()
            item.setRect(QRectF(self._selection_origin, self._selection_origin))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._selection_mode and self._selection_origin is not None:
            current = self.mapToScene(event.position().toPoint())
            item = self._ensure_selection_item()
            item.setRect(QRectF(self._selection_origin, current).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._selection_mode and self._selection_origin is not None and self.pixmap_item is not None:
            rect = QRectF(
                self._selection_origin, self.mapToScene(event.position().toPoint())
            ).normalized()
            self._selection_origin = None
            bounded = rect.intersected(self.pixmap_item.boundingRect())
            if bounded.width() >= 2 and bounded.height() >= 2:
                pixmap_size = self.pixmap_item.pixmap().size()
                scale_x = self._source_size.width() / pixmap_size.width() if pixmap_size.width() else 1.0
                scale_y = self._source_size.height() / pixmap_size.height() if pixmap_size.height() else 1.0
                original = QRect(
                    round(bounded.left() * scale_x),
                    round(bounded.top() * scale_y),
                    round(bounded.width() * scale_x),
                    round(bounded.height() * scale_y),
                )
                self.selectionMade.emit(original, QSize(self._source_size))
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
        if self.pixmap_item is None or event.angleDelta().y() == 0:
            # 纯横向滚动（触控板横扫）不应触发缩放
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
