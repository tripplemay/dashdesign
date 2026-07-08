"""Background update-manifest fetching for the desktop client."""

from __future__ import annotations

import json
import threading
import urllib.request

from PySide6.QtCore import QObject, Signal

from app_runtime import APP_VERSION


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
