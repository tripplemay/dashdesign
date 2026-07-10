"""Update-source selection: VPS primary via app-config, GitHub fallback."""

from __future__ import annotations

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
    assert signals.errors  # 两源都不可达才报错


def test_no_duplicate_request_when_fallback_equals_primary(monkeypatch) -> None:
    signals, calls = _run_fetch(monkeypatch, {GH: {"version": "3.0.0"}}, primary=GH, fallback=GH)
    assert signals.results == [({"version": "3.0.0"}, True)]
    assert calls == [GH]  # 去重，不重复请求同一地址
