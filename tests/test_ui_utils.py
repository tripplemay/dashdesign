"""Unit tests for the user-facing error hint mapping."""

from __future__ import annotations

import pytest

from ui.utils import friendly_error_hint


class TestFriendlyErrorHint:
    @pytest.mark.parametrize(
        "tail,keyword",
        [
            ("requests.exceptions.ConnectionError: HTTPSConnectionPool", "网络"),
            ("TimeoutError: timed out", "网络"),
            ("openai.AuthenticationError: 401 Incorrect API key provided", "密钥"),
            ("Error code: 429 - rate limit exceeded", "配额"),
            ("FileNotFoundError: [Errno 2] No such file or directory", "找不到"),
            ("PermissionError: [WinError 5] 拒绝访问", "权限"),
            ("OSError: [Errno 28] No space left on device", "磁盘"),
        ],
    )
    def test_known_patterns_mapped(self, tail: str, keyword: str) -> None:
        assert keyword in friendly_error_hint(tail)

    def test_unknown_error_returns_empty(self) -> None:
        assert friendly_error_hint("ZeroDivisionError: division by zero") == ""

    def test_empty_input(self) -> None:
        assert friendly_error_hint("") == ""
