"""Unit tests for the self-contained visual-prompt translation helper."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ is not a package and not on the default test path; the worker adds it
# at runtime (runpy from the scripts dir). Mirror that here to import the module.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import prompt_translate as pt  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _ok_payload(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


class TestContainsCjk:
    @pytest.mark.parametrize("text", ["未来数字课堂", "kids 教室", "科技"])
    def test_true_for_chinese(self, text: str) -> None:
        assert pt.contains_cjk(text)

    @pytest.mark.parametrize("text", ["bright classroom", "", "1024x1024", "!!!"])
    def test_false_without_chinese(self, text: str) -> None:
        assert not pt.contains_cjk(text)


class TestTranslateSkips:
    def test_pure_english_is_returned_untouched_without_network(self, monkeypatch) -> None:
        def _boom(*_a, **_k):  # 不应被调用
            raise AssertionError("requests.post should not be called for pure-English input")

        monkeypatch.setattr(pt.requests, "post", _boom)
        assert pt.translate_visual_prompt("bright classroom", "gpt-4o", base_url="http://x", api_key="k") == "bright classroom"

    def test_blank_text_returned(self, monkeypatch) -> None:
        monkeypatch.setattr(pt.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
        assert pt.translate_visual_prompt("   ", "gpt-4o", base_url="http://x", api_key="k") == "   "

    def test_missing_model_returned(self) -> None:
        assert pt.translate_visual_prompt("未来数字课堂", "", base_url="http://x", api_key="k") == "未来数字课堂"

    def test_missing_credentials_returned(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert pt.translate_visual_prompt("未来数字课堂", "gpt-4o") == "未来数字课堂"


class TestTranslateNetwork:
    def test_chinese_translated_on_success(self, monkeypatch) -> None:
        captured = {}

        def _post(url, headers=None, data=None, timeout=None):
            captured["url"] = url
            return _FakeResponse(200, _ok_payload("bright futuristic classroom"))

        monkeypatch.setattr(pt.requests, "post", _post)
        out = pt.translate_visual_prompt("明亮的未来感教室", "gpt-4o", base_url="http://gw/v1", api_key="k")
        assert out == "bright futuristic classroom"
        assert captured["url"] == "http://gw/v1/chat/completions"

    def test_http_error_falls_back_to_original(self, monkeypatch) -> None:
        monkeypatch.setattr(pt.requests, "post", lambda *a, **k: _FakeResponse(500, {}))
        assert pt.translate_visual_prompt("明亮的未来感教室", "gpt-4o", base_url="http://x", api_key="k") == "明亮的未来感教室"

    def test_request_exception_falls_back(self, monkeypatch) -> None:
        def _raise(*_a, **_k):
            raise pt.requests.RequestException("boom")

        monkeypatch.setattr(pt.requests, "post", _raise)
        assert pt.translate_visual_prompt("明亮的未来感教室", "gpt-4o", base_url="http://x", api_key="k") == "明亮的未来感教室"

    def test_empty_translation_falls_back(self, monkeypatch) -> None:
        monkeypatch.setattr(pt.requests, "post", lambda *a, **k: _FakeResponse(200, _ok_payload("   ")))
        assert pt.translate_visual_prompt("明亮的未来感教室", "gpt-4o", base_url="http://x", api_key="k") == "明亮的未来感教室"

    def test_malformed_response_falls_back(self, monkeypatch) -> None:
        monkeypatch.setattr(pt.requests, "post", lambda *a, **k: _FakeResponse(200, {"unexpected": True}))
        assert pt.translate_visual_prompt("明亮的未来感教室", "gpt-4o", base_url="http://x", api_key="k") == "明亮的未来感教室"

    def test_non_string_content_falls_back(self, monkeypatch) -> None:
        # 某些聚合网关可能返回 content-parts 列表而非字符串；必须降级而非崩溃。
        parts = [{"type": "text", "text": "bright classroom"}]
        monkeypatch.setattr(pt.requests, "post", lambda *a, **k: _FakeResponse(200, _ok_payload(parts)))
        assert pt.translate_visual_prompt("明亮的未来感教室", "gpt-4o", base_url="http://x", api_key="k") == "明亮的未来感教室"
