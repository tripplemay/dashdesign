#!/usr/bin/env python3
"""Entry point for the DashDesign desktop client.

Kept intentionally thin: the ``--worker`` branch must run before any Qt
import so workflow subprocesses never load PySide6. GUI code lives in the
``ui`` package; runtime helpers live in ``app_runtime``.
"""

from __future__ import annotations

import sys

from app_runtime import run_script_worker

__all__ = ["DashDesignQtApp", "create_application", "run_script_worker", "main"]

_LAZY_GUI_EXPORTS = {"DashDesignQtApp", "create_application"}


def __getattr__(name: str):
    # PEP 562: 惰性导出 GUI 符号，保证 `from desktop_qt_app import DashDesignQtApp,
    # create_application` 可用，同时让 --worker 路径完全不加载 Qt。
    if name in _LAZY_GUI_EXPORTS:
        from ui import main_window

        return getattr(main_window, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--worker":
        return run_script_worker(sys.argv[2], sys.argv[3:])
    from ui.main_window import DashDesignQtApp, create_application

    app = create_application(sys.argv)
    window = DashDesignQtApp()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
