"""Shared pytest fixtures.

Ensure a single QApplication exists before any Qt test runs. Some tests only
need a QCoreApplication (``QCoreApplication.instance() or QCoreApplication([])``),
but the widget tests need a QApplication, and the two singletons are mutually
exclusive — whichever is created first wins. Creating the QApplication up front
(session-scoped, autouse) lets the QCoreApplication tests reuse it.

Imports are lazy and ImportError is swallowed so the Qt-free test subset still
collects in an environment without PySide6.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _ensure_qapplication():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return None
    existing = QApplication.instance()
    if existing is not None:
        return existing
    from desktop_qt_app import create_application

    return create_application([])
