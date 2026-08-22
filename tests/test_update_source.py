"""Update-source selection: VPS primary via app-config, GitHub fallback."""

from __future__ import annotations

import pytest
import requests

from ui import updater

VPS = "https://dash.vpanel.cc/updates/update-manifest.json"
GH = "https://github.com/x/releases/latest/download/update-manifest.json"


class _Emitter:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    def emit(self, *args) -> None:
        self._sink.append(args)


class _Signals:
    def __init__(self) -> None:
        self.results: list = []
        self.errors: list = []
        self.result = _Emitter(self.results)
        self.error = _Emitter(self.errors)


class _InlineThread:
    """Runs the worker inline so the test is synchronous."""

    def __init__(self, target, daemon) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


def _run_fetch(monkeypatch, reachable: dict, primary: str, fallback: str = "") -> tuple:
    calls: list = []

    def fake_get(url: str) -> dict:
        calls.append(url)
        if url in reachable:
            return reachable[url]
        raise RuntimeError(f"unreachable: {url}")

    monkeypatch.setattr(updater, "_get_manifest", fake_get)
    monkeypatch.setattr(updater.threading, "Thread", _InlineThread)
    signals = _Signals()
    updater.fetch_update_manifest(primary, signals, silent=True, fallback_url=fallback)
    return signals, calls


def test_prefers_primary_when_reachable(monkeypatch) -> None:
    signals, calls = _run_fetch(monkeypatch, {VPS: {"version": "1.0.0"}}, primary=VPS, fallback=GH)
    assert signals.results == [({"version": "1.0.0"}, True)]
    assert calls == [VPS]  # 主源可达就不碰回退


def test_falls_back_to_github_when_primary_unreachable(monkeypatch) -> None:
    signals, calls = _run_fetch(monkeypatch, {GH: {"version": "2.0.0"}}, primary=VPS, fallback=GH)
    assert signals.results == [({"version": "2.0.0"}, True)]
    assert VPS in calls and GH in calls  # 先试 VPS，再回退 GitHub


def test_errors_when_both_unreachable(monkeypatch) -> None:
    signals, calls = _run_fetch(monkeypatch, {}, primary=VPS, fallback=GH)
    assert signals.results == []
    assert signals.errors == [
        (
            "所有更新源均不可用：\n"
            "dash.vpanel.cc: unreachable: https://dash.vpanel.cc/updates/update-manifest.json\n"
            "github.com: unreachable: https://github.com/x/releases/latest/download/update-manifest.json",
            True,
        )
    ]


def test_no_duplicate_request_when_fallback_equals_primary(monkeypatch) -> None:
    signals, calls = _run_fetch(monkeypatch, {GH: {"version": "3.0.0"}}, primary=GH, fallback=GH)
    assert signals.results == [({"version": "3.0.0"}, True)]
    assert calls == [GH]  # 去重，不重复请求同一地址


def test_accepts_multiple_fallback_urls(monkeypatch) -> None:
    calls: list[str] = []

    def fake_get(url: str) -> dict:
        calls.append(url)
        if url == GH:
            return {"version": "4.0.0"}
        raise RuntimeError("unreachable")

    monkeypatch.setattr(updater, "_get_manifest", fake_get)
    monkeypatch.setattr(updater.threading, "Thread", _InlineThread)
    signals = _Signals()
    updater.fetch_update_manifest(
        VPS,
        signals,
        silent=True,
        fallback_urls=("https://mirror.example/update-manifest.json", GH),
    )

    assert signals.results == [({"version": "4.0.0"}, True)]
    assert calls[-1] == GH


def test_http_403_moves_to_next_source_without_retries(monkeypatch) -> None:
    calls: list[str] = []

    class _Response:
        status_code = 403

    def fake_get(url: str) -> dict:
        calls.append(url)
        if url == VPS:
            raise requests.HTTPError("403", response=_Response())
        return {"version": "5.0.0"}

    monkeypatch.setattr(updater, "_get_manifest", fake_get)
    monkeypatch.setattr(updater.threading, "Thread", _InlineThread)
    signals = _Signals()
    updater.fetch_update_manifest(VPS, signals, silent=True, fallback_url=GH)

    assert signals.results == [({"version": "5.0.0"}, True)]
    assert calls == [VPS, GH]


def test_github_latest_url_is_cache_busted(monkeypatch) -> None:
    monkeypatch.setattr(updater.time, "time_ns", lambda: 123456)
    url = "https://github.com/tripplemay/dashdesign/releases/latest/download/update-manifest.json"
    result = updater._cache_busted_manifest_url(url)
    assert result == f"{url}?_dashdesign_cache=123456"


def test_mirror_url_is_not_cache_busted() -> None:
    assert updater._cache_busted_manifest_url(VPS) == VPS


def test_get_manifest_sends_no_cache_headers(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"version": "6.0.0"}

    def fake_get(url: str, **kwargs: object) -> _Response:
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(updater.requests, "get", fake_get)
    result = updater._get_manifest(
        "https://github.com/tripplemay/dashdesign/releases/latest/download/update-manifest.json"
    )

    assert result == {"version": "6.0.0"}
    assert "_dashdesign_cache=" in str(captured["url"])
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Cache-Control"] == "no-cache"
    assert headers["Pragma"] == "no-cache"


def test_get_manifest_rejects_non_object_json(monkeypatch) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list:
            return []

    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: _Response())
    with pytest.raises(ValueError, match="JSON 对象"):
        updater._get_manifest(VPS)
