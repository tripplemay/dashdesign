"""Non-blocking inline notification banner (toast replacement for modals)."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

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
        self._close_button = QPushButton("✕")
        self._close_button.setFixedWidth(28)
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
        self._icon_label.setPixmap(qta.icon(_KIND_ICONS[kind], color=color).pixmap(18, 18))
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
        self.show()

    def dismiss(self) -> None:
        self._timer.stop()
        self._kind = ""
        self.hide()

    def _run_action(self) -> None:
        callback = self._action_callback
        self.dismiss()
        if callback is not None:
            callback()
