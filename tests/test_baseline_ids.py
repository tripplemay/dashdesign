"""Unit tests for baseline_id validation & normalization.

The storable form is a lowercase slug (see ``_BASELINE_ID_RE``); the UI layer
normalizes user input up front so a bad id is caught before any expensive work
(e.g. calling the LLM to generate a baseline from a document).
"""

from __future__ import annotations

import pytest

from baseline.store import is_valid_baseline_id, normalize_baseline_id


class TestIsValidBaselineId:
    @pytest.mark.parametrize(
        "value",
        [
            "dashaicourse",
            "kids_coding_course",
            "kids-coding",
            "abc",
            "a1_",
            "x" * 81,  # max length (first char + 80)
        ],
    )
    def test_accepts_valid(self, value: str) -> None:
        assert is_valid_baseline_id(value)

    @pytest.mark.parametrize(
        "value",
        [
            "DASHaicourse",  # uppercase — the reported bug
            "ab",  # too short (<3)
            "x" * 82,  # too long (>81)
            "_abc",  # first char must be a letter/digit
            "-abc",
            "a b",  # space
            "少儿编程",  # non-latin
            "",  # empty
            "a.b",  # unsupported punctuation
            "aicourse\n",  # trailing newline: `$` would wrongly accept it, `\Z` must not
            "aicourse\n\n",
        ],
    )
    def test_rejects_invalid(self, value: str) -> None:
        assert not is_valid_baseline_id(value)


class TestNormalizeBaselineId:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("DASHaicourse", "dashaicourse"),
            ("DASH AI course", "dash_ai_course"),
            ("  Kids Coding!  ", "kids_coding"),
            ("kids-coding", "kids-coding"),  # existing hyphens preserved
            ("__abc__", "abc"),  # leading/trailing separators trimmed
            ("a!!!b", "a_b"),  # a run of unsupported chars collapses to one '_'
        ],
    )
    def test_normalizes(self, raw: str, expected: str) -> None:
        assert normalize_baseline_id(raw) == expected

    def test_normalized_result_is_valid(self) -> None:
        assert is_valid_baseline_id(normalize_baseline_id("DASHaicourse"))

    @pytest.mark.parametrize("raw", ["少儿编程", "!!!", "  ", "--"])
    def test_unusable_input_yields_invalid(self, raw: str) -> None:
        # No usable latin/digit characters -> normalizes to empty/too-short, so
        # is_valid rejects it and the dialog can prompt the user to pick an id.
        assert not is_valid_baseline_id(normalize_baseline_id(raw))

    def test_truncates_to_max_length(self) -> None:
        assert len(normalize_baseline_id("a" * 200)) == 81
