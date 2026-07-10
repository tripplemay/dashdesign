"""Background update-manifest fetching and artifact downloading for the client.

The heavy lifting (manifest evaluation, verified streaming download) lives in the
Qt-free ``update_core`` module; this layer only adapts it to Qt signals so the
GUI thread stays responsive.
"""

from __future__ import annotations

import threading
from typing import Callable

import requests
from PySide6.QtCore import QObject, Signal

from app_runtime import APP_VERSION
from update_core import (
    DownloadCancelled,
    UpdateError,
    UpdateInfo,
    default_download_path,
    download_to_temp,
)

# manifest 走 GitHub 的 releases/latest/download → 3 跳重定向到 release-assets，
# GitHub 略慢时旧的 urllib 12s 超时就失败（下载用 30s 反而能成）。改用 app 里已
# 稳定工作的 requests（自带 certifi、跟随重定向），放宽超时并重试几次。
_MANIFEST_TIMEOUT = 20
_MANIFEST_RETRIES = 3


class UpdateSignals(QObject):
    result = Signal(dict, bool)
    error = Signal(str, bool)


def _get_manifest(url: str) -> dict:
    resp = requests.get(
        url,
        headers={"User-Agent": f"DashDesign/{APP_VERSION}"},
        timeout=_MANIFEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_update_manifest(
    manifest_url: str,
    signals: UpdateSignals,
    silent: bool,
    fallback_url: str = "",
) -> None:
    """Fetch the manifest on a daemon thread and emit the outcome via signals.

    Tries ``manifest_url`` first (the VPS mirror, reachable where GitHub is not),
    then ``fallback_url`` (the baked GitHub URL) if the primary is unreachable —
    so a down/blocked primary still resolves. The manifest that wins also decides
    the download host (its ``platforms.*.url``), keeping fetch and download on
    the same source.
    """
    # 去重 + 去空：回退地址与主源相同或为空时不重复请求。
    urls = [u for u in (manifest_url, fallback_url) if u]
    seen: "set[str]" = set()
    ordered = [u for u in urls if not (u in seen or seen.add(u))]

    def worker() -> None:
        last_exc: "Exception | None" = None
        for url in ordered:
            for _ in range(_MANIFEST_RETRIES):
                try:
                    signals.result.emit(_get_manifest(url), silent)
                    return
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
        signals.error.emit(str(last_exc) if last_exc else "no manifest url", silent)

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
