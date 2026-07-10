"""Non-blocking inline notification banner (toast replacement for modals)."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, QTimer
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

import qtawesome as qta

from ui import theme

_KIND_ICONS = {
    "info": "mdi6.information-outline",
    "success": "mdi6.check-circle-outline",
    "warning": "mdi6.alert-outline",
    "error": "mdi6.alert-circle-outline",
}


class InfoBanner(QWidget):
    """Inline banner shown above the workspace; styled per `kind` in QSS."""

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setObjectName("InfoBanner")
        self.setProperty("kind", "info")

        self._icon_label = QLabel()
        self._text_label = QLabel()
        self._text_label.setWordWrap(True)
        self._action_button = QPushButton()
        self._action_button.setCursor(self._action_button.cursor())
        self._action_button.hide()
        # 关闭按钮用图标而不是文字 "✕"：文字符号随系统字体渲染不一，
        # 也无法跟随 kind 颜色；图标在 show_message 里按 kind 着色。
        self._close_button = QPushButton()
        self._close_button.setFixedWidth(28)
        self._close_button.setToolTip("关闭")
        self._close_button.clicked.connect(self.dismiss)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self._icon_label)
        layout.addWidget(self._text_label, 1)
        layout.addWidget(self._action_button)
        layout.addWidget(self._close_button)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._action_callback = None
        self._action_button.clicked.connect(self._run_action)
        self._kind = ""

        # 150ms 淡入/淡出：banner 出现与消失不再硬切。
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fading_out = False
        self._anim.finished.connect(self._on_fade_finished)
        self.hide()

    def _on_fade_finished(self) -> None:
        if self._fading_out:
            self._fading_out = False
            self.hide()

    def kind(self) -> str:
        """The kind of the currently shown banner, or "" when hidden."""
        return self._kind

    def show_message(
        self,
        kind: str,
        text: str,
        action_label: str = "",
        action_callback=None,  # type: ignore[no-untyped-def]
        timeout_ms: int = 0,
    ) -> None:
        if kind not in _KIND_ICONS:
            kind = "info"
        self._kind = kind
        self.setProperty("kind", kind)
        self.style().unpolish(self)
        self.style().polish(self)

        tokens = theme.current_tokens()
        color = tokens.get(f"{kind}_fg", tokens["subtitle_fg"])
        # 传入 devicePixelRatio：固定 18px 物理像素的 pixmap 在 HiDPI 屏上会发虚。
        ratio = self.devicePixelRatioF()
        self._icon_label.setPixmap(
            qta.icon(_KIND_ICONS[kind], color=color).pixmap(QSize(18, 18), ratio)
        )
        self._close_button.setIcon(qta.icon("mdi6.close", color=color))
        self._text_label.setText(text)

        self._action_callback = action_callback
        if action_label and action_callback is not None:
            self._action_button.setText(action_label)
            self._action_button.show()
        else:
            self._action_button.hide()

        self._timer.stop()
        if timeout_ms > 0:
            self._timer.start(timeout_ms)
        # 淡入。先置 _fading_out=False：stop() 会发 finished，避免误 hide 新横幅。
        self._fading_out = False
        self._anim.stop()
        already_visible = self.isVisible()
        self.show()
        if not already_visible:
            self._opacity.setOpacity(0.0)
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.start()
        else:
            self._opacity.setOpacity(1.0)

    def dismiss(self) -> None:
        self._timer.stop()
        self._kind = ""
        if not self.isVisible():
            return
        # 淡出后再隐藏。先在标志为 False 时 stop（stop 会发 finished），
        # 避免上一段动画的 finished 直接把横幅藏掉，再置位启动淡出。
        self._fading_out = False
        self._anim.stop()
        self._fading_out = True
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _run_action(self) -> None:
        callback = self._action_callback
        self.dismiss()
        if callback is not None:
            callback()
