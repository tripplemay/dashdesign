"""Background update-manifest fetching and artifact downloading for the client.

The heavy lifting (manifest evaluation, verified streaming download) lives in the
Qt-free ``update_core`` module; this layer only adapts it to Qt signals so the
GUI thread stays responsive.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from typing import Callable

from PySide6.QtCore import QObject, Signal

from app_runtime import APP_VERSION
from update_core import (
    DownloadCancelled,
    UpdateError,
    UpdateInfo,
    default_download_path,
    download_to_temp,
)


class UpdateSignals(QObject):
    result = Signal(dict, bool)
    error = Signal(str, bool)


def fetch_update_manifest(manifest_url: str, signals: UpdateSignals, silent: bool) -> None:
    """Fetch the manifest on a daemon thread and emit the outcome via signals."""

    def worker() -> None:
        try:
            request = urllib.request.Request(
                manifest_url,
                headers={"User-Agent": f"DashDesign/{APP_VERSION}"},
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            signals.result.emit(payload, silent)
        except Exception as exc:  # noqa: BLE001
            signals.error.emit(str(exc), silent)

    threading.Thread(target=worker, daemon=True).start()


class DownloadSignals(QObject):
    progress = Signal(int, int)  # downloaded_bytes, total_bytes
    done = Signal(str)  # local path of the verified artifact
    error = Signal(str)  # user-facing failure message
    cancelled = Signal()


def download_update(
    info: UpdateInfo,
    signals: DownloadSignals,
    should_cancel: Callable[[], bool],
) -> None:
    """Download and verify ``info`` on a daemon thread, reporting via signals.

    ``should_cancel`` is polled from the worker thread; when it returns True the
    partial file is discarded and ``cancelled`` is emitted instead of ``done``.
    """
    dest = default_download_path(info.url)

    def worker() -> None:
        try:
            result = download_to_temp(
                info.url,
                dest,
                progress_cb=lambda d, t: signals.progress.emit(d, t or info.size),
                expected_sha256=info.sha256,
                should_cancel=should_cancel,
            )
            signals.done.emit(str(result))
        except DownloadCancelled:
            signals.cancelled.emit()
        except UpdateError as exc:
            signals.error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            signals.error.emit(str(exc))

    threading.Thread(target=worker, daemon=True).start()
