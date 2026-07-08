"""Application-wide stylesheet for the DashDesign desktop client."""

from __future__ import annotations

_APP_QSS = """
QMainWindow, QWidget {
    font-size: 13px;
}
QListWidget {
    background: #f4f4f4;
    border-right: 1px solid #d8d8d8;
    padding: 10px 8px;
}
QListWidget::item {
    border-radius: 7px;
    padding: 8px 10px;
}
QListWidget::item:selected {
    background: #222222;
    color: #ffffff;
}
QLabel#Title {
    font-size: 22px;
    font-weight: 700;
}
QLabel#Subtitle {
    color: #666666;
}
QGroupBox {
    border: 1px solid #d9d9d9;
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px;
    background: #ffffff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #333333;
    font-weight: 600;
}
QPushButton {
    min-height: 24px;
    padding: 4px 12px;
    border-radius: 6px;
}
QPushButton#PrimaryButton {
    background: #1f1f1f;
    color: #ffffff;
}
QRadioButton, QCheckBox {
    color: #222222;
    min-height: 24px;
    spacing: 8px;
}
QRadioButton::indicator, QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #777777;
    background: #ffffff;
}
QRadioButton::indicator {
    border-radius: 8px;
}
QRadioButton::indicator:checked {
    background: #222222;
    border: 1px solid #222222;
}
QCheckBox::indicator {
    border-radius: 3px;
}
QCheckBox::indicator:checked {
    background: #222222;
    border: 2px solid #222222;
}
QLineEdit, QComboBox {
    min-height: 26px;
    border: 1px solid #d9d9d9;
    border-radius: 6px;
    padding: 3px 8px;
    background: #ffffff;
    color: #222222;
}
QPlainTextEdit {
    background: #111111;
    color: #eeeeee;
    border-radius: 8px;
    padding: 8px;
    font-family: Menlo, Monaco, Consolas, monospace;
    font-size: 12px;
}
QPlainTextEdit#BaselineSummary {
    background: #ffffff;
    color: #222222;
    border: 1px solid #d9d9d9;
    border-radius: 8px;
    padding: 10px;
    font-family: Menlo, Monaco, Consolas, monospace;
    font-size: 12px;
}
QPlainTextEdit#TextPrompt {
    background: #ffffff;
    color: #222222;
    border: 1px solid #d9d9d9;
    border-radius: 8px;
    padding: 10px;
    font-family: Menlo, Monaco, Consolas, monospace;
    font-size: 12px;
}
QWidget#PreviewPanel {
    background: #ffffff;
    border: 1px solid #d9d9d9;
    border-radius: 8px;
}
"""


def app_stylesheet() -> str:
    return _APP_QSS
