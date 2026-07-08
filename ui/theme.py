"""Token-based theming for the DashDesign desktop client.

Two-layer design: per-theme semantic tokens (below) are rendered into an
overlay QSS template and applied on top of the qdarktheme base stylesheet.
``ThemeManager`` owns the current mode (system / light / dark), persists it
via QSettings, and re-applies everything when the OS color scheme changes.
"""

from __future__ import annotations

from string import Template

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import QApplication

THEME_MODES = ("system", "light", "dark")
_SETTINGS_KEY = "appearance/mode"

TOKENS_LIGHT = {
    "accent": "#2563EB",
    "accent_hover": "#1D4ED8",
    "accent_fg": "#FFFFFF",
    "nav_fg": "#3A3F47",
    "sidebar_bg": "#EEF0F3",
    "sidebar_item_hover": "rgba(0, 0, 0, 0.05)",
    "sidebar_item_selected_bg": "rgba(37, 99, 235, 0.12)",
    "sidebar_item_selected_fg": "#1D4ED8",
    "card_bg": "#FFFFFF",
    "card_border": "#E2E4E8",
    "subtitle_fg": "#5C6370",
    "log_bg": "#14161A",
    "log_fg": "#D8DEE9",
    "canvas_bg": "#2E3138",
}

TOKENS_DARK = {
    "accent": "#5B9BF8",
    "accent_hover": "#7DB0FA",
    "accent_fg": "#0C0E12",
    "nav_fg": "#C9CFD8",
    "sidebar_bg": "#1A1C20",
    "sidebar_item_hover": "rgba(255, 255, 255, 0.06)",
    "sidebar_item_selected_bg": "rgba(91, 155, 248, 0.16)",
    "sidebar_item_selected_fg": "#8AB8FA",
    "card_bg": "#24262B",
    "card_border": "#33363D",
    "subtitle_fg": "#9AA0AA",
    "log_bg": "#101215",
    "log_fg": "#C8CEDA",
    "canvas_bg": "#26282D",
}

_MONO_FONTS = '"SF Mono", Menlo, Monaco, Consolas, "Courier New", monospace'

_OVERLAY_TEMPLATE = Template(
    """
QWidget {
    font-size: 14px;
}
QLabel#Title {
    font-size: 20px;
    font-weight: 600;
}
QLabel#Subtitle {
    color: $subtitle_fg;
    font-size: 12px;
}
QListWidget#NavList {
    background: $sidebar_bg;
    border: none;
    border-right: 1px solid $card_border;
    padding: 10px 6px;
}
QListWidget#NavList::item {
    border-radius: 6px;
    padding: 8px 10px;
    margin: 1px 2px;
    color: $nav_fg;
}
QListWidget#NavList::item:hover {
    background: $sidebar_item_hover;
}
QListWidget#NavList::item:selected {
    background: $sidebar_item_selected_bg;
    color: $sidebar_item_selected_fg;
    font-weight: 600;
}
QGroupBox {
    background: $card_bg;
    border: 1px solid $card_border;
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    font-weight: 600;
}
QPushButton {
    min-height: 26px;
    padding: 4px 14px;
    border-radius: 6px;
}
QPushButton#PrimaryButton {
    background: $accent;
    color: $accent_fg;
    border: 1px solid $accent;
    font-weight: 600;
}
QPushButton#PrimaryButton:hover {
    background: $accent_hover;
    border-color: $accent_hover;
}
QPushButton#PrimaryButton:pressed {
    background: $accent;
}
QPushButton#PrimaryButton:disabled {
    background: transparent;
    color: $subtitle_fg;
    border: 1px solid $card_border;
}
QLineEdit, QComboBox, QAbstractSpinBox {
    min-height: 26px;
    border-radius: 6px;
}
QPlainTextEdit {
    border-radius: 6px;
}
QPlainTextEdit#RunLog {
    background: $log_bg;
    color: $log_fg;
    border: 1px solid $card_border;
    border-radius: 8px;
    padding: 8px;
    font-family: $mono_fonts;
    font-size: 12px;
}
QPlainTextEdit#BaselineSummary {
    font-family: $mono_fonts;
    font-size: 12px;
}
QWidget#PreviewPanel {
    background: $card_bg;
    border: 1px solid $card_border;
    border-radius: 8px;
}
QGraphicsView#PreviewCanvas {
    background: $canvas_bg;
    border: 1px solid $card_border;
    border-radius: 6px;
}
QStatusBar {
    color: $subtitle_fg;
}
"""
)

# 旧版单主题 QSS 兜底：qdarktheme 不可用时保证界面仍可读。
_FALLBACK_QSS = """
QWidget { font-size: 14px; }
QLabel#Title { font-size: 20px; font-weight: 600; }
QLabel#Subtitle { color: #5C6370; }
QGroupBox { border: 1px solid #D9D9D9; border-radius: 8px; margin-top: 14px; padding: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; font-weight: 600; }
QPushButton { min-height: 26px; padding: 4px 14px; border-radius: 6px; }
"""


def tokens_for(resolved: str) -> dict[str, str]:
    return TOKENS_DARK if resolved == "dark" else TOKENS_LIGHT


def overlay_qss(resolved: str) -> str:
    values = dict(tokens_for(resolved))
    values["mono_fonts"] = _MONO_FONTS
    return _OVERLAY_TEMPLATE.substitute(values)


class ThemeManager(QObject):
    """Applies theme mode and notifies listeners with the resolved variant."""

    changed = Signal(str)  # "light" | "dark"

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        self._settings = QSettings()
        stored = str(self._settings.value(_SETTINGS_KEY, "system"))
        self._mode = stored if stored in THEME_MODES else "system"
        app.styleHints().colorSchemeChanged.connect(self._on_color_scheme_changed)

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in THEME_MODES:
            mode = "system"
        self._mode = mode
        self._settings.setValue(_SETTINGS_KEY, mode)
        self.refresh()

    def resolved(self) -> str:
        if self._mode in ("light", "dark"):
            return self._mode
        scheme = self._app.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"

    def refresh(self) -> None:
        resolved = self.resolved()
        try:
            import qdarktheme

            qdarktheme.setup_theme(
                theme=resolved,
                corner_shape="rounded",
                custom_colors={"primary": tokens_for(resolved)["accent"]},
                additional_qss=overlay_qss(resolved),
            )
        except Exception as exc:  # noqa: BLE001 - 主题失败不能阻止应用启动
            print(f"[theme] qdarktheme 应用失败，回退基础样式: {exc}")
            self._app.setStyleSheet(_FALLBACK_QSS)
        self.changed.emit(resolved)

    def _on_color_scheme_changed(self, scheme) -> None:  # type: ignore[no-untyped-def]
        if self._mode == "system":
            self.refresh()


_manager: "ThemeManager | None" = None


def init_theme(app: QApplication) -> ThemeManager:
    global _manager
    _manager = ThemeManager(app)
    _manager.refresh()
    return _manager


def manager() -> "ThemeManager | None":
    return _manager


def current_tokens() -> dict[str, str]:
    if _manager is not None:
        return tokens_for(_manager.resolved())
    return dict(TOKENS_LIGHT)
