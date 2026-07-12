"""Unit tests for the Qt-free auto-update core (manifest eval + verified download)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from update_core import (
    ChecksumMismatch,
    DownloadCancelled,
    UpdateInfo,
    default_download_path,
    download_to_temp,
    evaluate_manifest,
)


def _manifest(version: str = "0.2.0", **platform_fields: object) -> dict:
    windows = {"url": "https://example/setup.exe", "sha256": "abc", "size": 123}
    windows.update(platform_fields)
    return {
        "version": version,
        "notes": "新增自动更新",
        "platforms": {
            "windows": windows,
            "macos": {"url": "https://example/app.dmg", "sha256": "def", "size": 456},
        },
    }


class TestEvaluateManifest:
    def test_newer_version_returns_update_info(self) -> None:
        info = evaluate_manifest(_manifest("0.2.0"), "0.1.0", "windows")
        assert isinstance(info, UpdateInfo)
        assert info.version == "0.2.0"
        assert info.url == "https://example/setup.exe"
        assert info.sha256 == "abc"
        assert info.size == 123
        assert info.notes == "新增自动更新"

    def test_equal_version_returns_none(self) -> None:
        assert evaluate_manifest(_manifest("0.1.0"), "0.1.0", "windows") is None

    def test_older_version_returns_none(self) -> None:
        assert evaluate_manifest(_manifest("0.1.0"), "0.2.0", "windows") is None

    def test_missing_version_returns_none(self) -> None:
        assert evaluate_manifest({"platforms": {}}, "0.1.0", "windows") is None

    def test_newer_but_platform_missing_url_returns_none(self) -> None:
        manifest = {"version": "0.2.0", "platforms": {"macos": {"url": "x"}}}
        assert evaluate_manifest(manifest, "0.1.0", "windows") is None

    def test_selects_requested_platform(self) -> None:
        info = evaluate_manifest(_manifest("0.2.0"), "0.1.0", "macos")
        assert info is not None
        assert info.url == "https://example/app.dmg"
        assert info.size == 456

    def test_top_level_url_fallback(self) -> None:
        manifest = {"version": "0.2.0", "url": "https://example/fallback"}
        info = evaluate_manifest(manifest, "0.1.0", "windows")
        assert info is not None
        assert info.url == "https://example/fallback"

    def test_non_numeric_size_defaults_to_zero(self) -> None:
        info = evaluate_manifest(_manifest("0.2.0", size="oops"), "0.1.0", "windows")
        assert info is not None
        assert info.size == 0

    def test_malformed_manifest_returns_none(self) -> None:
        assert evaluate_manifest("not a dict", "0.1.0", "windows") is None  # type: ignore[arg-type]
        assert evaluate_manifest({"version": "0.2.0", "platforms": "bad"}, "0.1.0", "windows") is None


class TestDefaultDownloadPath:
    def test_uses_url_basename(self) -> None:
        path = default_download_path("https://example/a/b/DashDesign-0.2.0-windows-setup.exe")
        assert path.name == "DashDesign-0.2.0-windows-setup.exe"

    def test_falls_back_when_no_basename(self) -> None:
        path = default_download_path("https://example/")
        assert path.name  # 非空


def _write_payload(tmp_path: Path, size: int = 5000) -> Path:
    src = tmp_path / "payload.bin"
    src.write_bytes(bytes(range(256)) * (size // 256 + 1))
    return src


class TestDownloadToTemp:
    def test_downloads_and_verifies(self, tmp_path: Path) -> None:
        src = _write_payload(tmp_path)
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        dest = tmp_path / "out.bin"
        result = download_to_temp(src.as_uri(), dest, expected_sha256=digest)
        assert result == dest
        assert dest.read_bytes() == src.read_bytes()

    def test_uppercase_checksum_accepted(self, tmp_path: Path) -> None:
        src = _write_payload(tmp_path)
        digest = hashlib.sha256(src.read_bytes()).hexdigest().upper()
        dest = tmp_path / "out.bin"
        assert download_to_temp(src.as_uri(), dest, expected_sha256=digest) == dest

    def test_checksum_mismatch_raises_and_removes_file(self, tmp_path: Path) -> None:
        src = _write_payload(tmp_path)
        dest = tmp_path / "out.bin"
        with pytest.raises(ChecksumMismatch):
            download_to_temp(src.as_uri(), dest, expected_sha256="deadbeef")
        assert not dest.exists()

    def test_progress_callback_monotonic_and_complete(self, tmp_path: Path) -> None:
        src = _write_payload(tmp_path, size=8000)
        dest = tmp_path / "out.bin"
        calls: list[tuple[int, int]] = []
        download_to_temp(src.as_uri(), dest, progress_cb=lambda d, t: calls.append((d, t)))
        downloaded = [d for d, _ in calls]
        assert downloaded == sorted(downloaded)
        assert downloaded[-1] == src.stat().st_size

    def test_empty_checksum_skips_verification(self, tmp_path: Path) -> None:
        src = _write_payload(tmp_path)
        dest = tmp_path / "out.bin"
        assert download_to_temp(src.as_uri(), dest, expected_sha256="") == dest

    def test_cancellation_raises_and_cleans_up(self, tmp_path: Path) -> None:
        src = _write_payload(tmp_path)
        dest = tmp_path / "out.bin"
        with pytest.raises(DownloadCancelled):
            download_to_temp(src.as_uri(), dest, should_cancel=lambda: True)
        assert not dest.exists()


class TestSslContext:
    def test_ssl_context_backed_by_certifi(self) -> None:
        import ssl

        from update_core import _ssl_context

        context = _ssl_context()
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED

    def test_https_download_verifies_with_certifi_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Fixes "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer
        # certificate": the download must pass a certifi-backed context to
        # urlopen, not urllib's machine-dependent default.
        import ssl

        import update_core

        captured: dict[str, object] = {}

        class _FakeResponse:
            def __init__(self) -> None:
                self._chunks = iter([b"payload", b""])

            def __enter__(self) -> "_FakeResponse":
                return self

            def __exit__(self, *_exc: object) -> bool:
                return False

            def getheader(self, _name: str) -> str:
                return "7"

            def read(self, _size: int) -> bytes:
                return next(self._chunks)

        def _fake_urlopen(request: object, timeout: object = None, context: object = None):
            captured["context"] = context
            return _FakeResponse()

        monkeypatch.setattr(update_core.urllib.request, "urlopen", _fake_urlopen)
        dest = tmp_path / "out.bin"
        update_core.download_to_temp("https://example.com/DashDesign-setup.exe", dest)
        assert isinstance(captured["context"], ssl.SSLContext)
        assert dest.read_bytes() == b"payload"
