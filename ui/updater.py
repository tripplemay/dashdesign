"""Background update-manifest fetching and artifact downloading for the client.

The heavy lifting (manifest evaluation, verified streaming download) lives in the
Qt-free ``update_core`` module; this layer only adapts it to Qt signals so the
GUI thread stays responsive.
"""

from __future__ import annotations

import threading
import time
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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

# The primary release builds use the VPS mirror. GitHub remains a compatibility
# fallback for older builds and for a temporary mirror outage. Keep requests
# (rather than urllib) here: it bundles certifi and follows redirects reliably.
_MANIFEST_TIMEOUT = 20
_MANIFEST_RETRIES = 3
_GITHUB_HOSTS = {"github.com", "www.github.com"}


def _cache_busted_manifest_url(url: str) -> str:
    """Avoid stale signed redirects from GitHub's ``latest/download`` route.

    GitHub answers that route with a short-lived redirect to
    ``release-assets.githubusercontent.com``. Some proxies cache the redirect
    longer than its signature lifetime and turn a valid request into HTTP 403.
    A per-request query value forces GitHub to mint a fresh redirect. Mirror
    URLs are left untouched so their normal cache headers remain effective.
    """
    parsed = urlsplit(url)
    host = parsed.netloc.rsplit("@", 1)[-1].split(":", 1)[0].lower()
    path = parsed.path.rstrip("/").lower()
    if host not in _GITHUB_HOSTS or not path.endswith(
        "/releases/latest/download/update-manifest.json"
    ):
        return url
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("_dashdesign_cache", str(time.time_ns())))
    return urlunsplit(parsed._replace(query=urlencode(query)))


class UpdateSignals(QObject):
    result = Signal(dict, bool)
    error = Signal(str, bool)


def _get_manifest(url: str) -> dict:
    resp = requests.get(
        _cache_busted_manifest_url(url),
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": f"DashDesign/{APP_VERSION}",
        },
        timeout=_MANIFEST_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise ValueError("更新 manifest 必须是 JSON 对象")
    return payload


def _format_source_error(url: str, exc: Exception) -> str:
    host = urlsplit(url).netloc or url
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status:
        return f"{host} 返回 HTTP {status}"
    if isinstance(exc, requests.Timeout):
        return f"{host} 连接超时"
    if isinstance(exc, requests.ConnectionError):
        return f"{host} 无法连接"
    detail = str(exc).strip() or type(exc).__name__
    return f"{host}: {detail}"


def fetch_update_manifest(
    manifest_url: str,
    signals: UpdateSignals,
    silent: bool,
    fallback_url: str = "",
    fallback_urls: "tuple[str, ...]" = (),
) -> None:
    """Fetch the manifest on a daemon thread and emit the outcome via signals.

    Tries the supplied URLs in order. The manifest that wins also decides the
    download host (its ``platforms.*.url``), keeping fetch and download on the
    same source.
    """
    # 去重 + 去空：回退地址与主源相同或为空时不重复请求。
    urls = [u for u in (manifest_url, fallback_url, *fallback_urls) if u]
    seen: "set[str]" = set()
    ordered: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        ordered.append(url)

    def worker() -> None:
        errors: list[str] = []
        for url in ordered:
            source_exc: "Exception | None" = None
            for _ in range(_MANIFEST_RETRIES):
                try:
                    signals.result.emit(_get_manifest(url), silent)
                    return
                except Exception as exc:  # noqa: BLE001
                    source_exc = exc
                    # Authentication/permission and missing-resource responses
                    # are deterministic. Move to the next source immediately;
                    # retries remain useful for timeouts and transient 5xxs.
                    response = getattr(exc, "response", None)
                    status = getattr(response, "status_code", None)
                    if status in {400, 401, 403, 404}:
                        break
            if source_exc is not None:
                errors.append(_format_source_error(url, source_exc))
        if errors:
            signals.error.emit("所有更新源均不可用：\n" + "\n".join(errors), silent)
        else:
            signals.error.emit("未配置更新地址", silent)

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
