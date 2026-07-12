"""Qt-free auto-update core: manifest evaluation and verified downloading.

This module is imported by the Qt updater layer (``ui/updater.py``) and by the
unit tests. It MUST stay free of PySide6 imports so the logic can be exercised
headlessly and reused from worker/CLI contexts.
"""

from __future__ import annotations

import hashlib
import ssl
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

from app_runtime import APP_VERSION, version_tuple


class UpdateError(Exception):
    """Base class for recoverable update failures."""


class ChecksumMismatch(UpdateError):
    """The downloaded artifact did not match the expected sha256."""


class DownloadCancelled(UpdateError):
    """The caller requested cancellation while downloading."""


@dataclass(frozen=True)
class UpdateInfo:
    """A newer build advertised by the manifest for the current platform."""

    version: str
    url: str
    sha256: str
    size: int
    notes: str


def evaluate_manifest(
    manifest: object,
    current_version: str,
    platform: str,
) -> Optional[UpdateInfo]:
    """Return an :class:`UpdateInfo` when ``manifest`` advertises a strictly
    newer build that has a usable download URL for ``platform``.

    Returns ``None`` for every non-actionable case (up to date, malformed
    manifest, or no download URL for this platform). Never raises on bad input.
    """
    if not isinstance(manifest, dict):
        return None
    latest = str(manifest.get("version", "")).strip()
    if not latest:
        return None
    if version_tuple(latest) <= version_tuple(current_version):
        return None

    platforms = manifest.get("platforms")
    platform_info = platforms.get(platform) if isinstance(platforms, dict) else None
    if not isinstance(platform_info, dict):
        platform_info = {}

    url = str(platform_info.get("url") or manifest.get("url") or "").strip()
    if not url:
        return None

    sha256 = str(platform_info.get("sha256") or "").strip().lower()
    try:
        size = int(platform_info.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    notes = str(manifest.get("notes") or "")
    return UpdateInfo(version=latest, url=url, sha256=sha256, size=size, notes=notes)


def default_download_path(url: str) -> Path:
    """Build a temp-dir destination path from ``url``'s basename."""
    name = unquote(urlsplit(url).path).rsplit("/", 1)[-1].strip()
    if not name:
        name = "dashdesign-update.download"
    return Path(tempfile.gettempdir()) / name


def _content_length(response: object) -> int:
    header = None
    getheader = getattr(response, "getheader", None)
    if callable(getheader):
        header = getheader("Content-Length")
    try:
        return int(header) if header else 0
    except (TypeError, ValueError):
        return 0


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _ssl_context() -> "ssl.SSLContext | None":
    """Trust store for HTTPS downloads.

    The frozen Windows build's default SSL context can fail to build a chain to
    GitHub's CA ("unable to get local issuer certificate") on machines whose
    local root store lacks the needed roots (common when Windows automatic root
    updates are disabled) — even though the manifest fetch already succeeds via
    ``requests``, which bundles certifi. Verify against certifi's portable CA
    bundle here so the download trusts the same roots regardless of the machine.
    Returns ``None`` (urllib's default) only if certifi is somehow unavailable.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - certifi missing → fall back to default
        return None


def download_to_temp(
    url: str,
    dest: Path,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    expected_sha256: str = "",
    *,
    chunk_size: int = 256 * 1024,
    timeout: float = 30.0,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Path:
    """Stream ``url`` into ``dest`` while computing its sha256.

    ``progress_cb(downloaded_bytes, total_bytes)`` fires as data arrives;
    ``total_bytes`` is 0 when the server sends no ``Content-Length``.
    ``should_cancel`` is polled before each chunk. Raises
    :class:`DownloadCancelled` on cancellation and :class:`ChecksumMismatch`
    when ``expected_sha256`` is set and does not match; the partial file is
    removed in both cases.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url, headers={"User-Agent": f"DashDesign/{APP_VERSION}"}
    )
    # Only HTTPS needs a trust store; file:// (tests) and other schemes ignore it.
    context = _ssl_context() if urlsplit(url).scheme == "https" else None
    digest = hashlib.sha256()
    downloaded = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            total = _content_length(response)
            if progress_cb:
                progress_cb(0, total)
            with dest.open("wb") as handle:
                while True:
                    if should_cancel is not None and should_cancel():
                        raise DownloadCancelled("已取消下载")
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total)
    except DownloadCancelled:
        _safe_unlink(dest)
        raise
    except (OSError, ValueError) as exc:  # network/urllib/file errors
        _safe_unlink(dest)
        raise UpdateError(str(exc)) from exc

    if expected_sha256:
        actual = digest.hexdigest()
        if actual != expected_sha256.strip().lower():
            _safe_unlink(dest)
            raise ChecksumMismatch(
                f"安装包校验失败：期望 {expected_sha256[:12]}…，实际 {actual[:12]}…"
            )
    return dest
