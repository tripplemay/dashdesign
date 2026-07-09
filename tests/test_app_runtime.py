"""Unit tests for Qt-free runtime helpers."""

from __future__ import annotations

import os
import time
from pathlib import Path

from app_runtime import (
    evidenced_text,
    first_image,
    first_output_image,
    install_roots,
    path_is_within,
    resolve_output_dir,
    text_list,
    version_tuple,
    worker_prefix,
)


class TestVersionTuple:
    def test_plain_semver(self) -> None:
        assert version_tuple("1.2.3") == (1, 2, 3)

    def test_v_prefix_stripped(self) -> None:
        assert version_tuple("v0.2.0") == (0, 2, 0)

    def test_non_digit_chars_dropped(self) -> None:
        assert version_tuple("1.2.0-beta") == (1, 2, 0)

    def test_empty_chunk_becomes_zero(self) -> None:
        assert version_tuple("1..3") == (1, 0, 3)

    def test_comparison_semantics(self) -> None:
        assert version_tuple("0.2.0") > version_tuple("0.1.9")


class TestEvidencedText:
    def test_plain_string(self) -> None:
        assert evidenced_text(" 你好 ") == "你好"

    def test_dict_with_text(self) -> None:
        assert evidenced_text({"text": "证据 ", "source": "doc"}) == "证据"

    def test_none_and_empty(self) -> None:
        assert evidenced_text(None) == ""
        assert evidenced_text({}) == ""


class TestTextList:
    def test_filters_empty_entries(self) -> None:
        values = ["甲", {"text": "乙"}, "", {"text": " "}, None]
        assert text_list(values) == ["甲", "乙"]

    def test_non_list_returns_empty(self) -> None:
        assert text_list("不是列表") == []


class TestWorkerPrefix:
    def test_dev_mode_invokes_shim_with_worker_flag(self) -> None:
        prefix = worker_prefix()
        assert prefix[-1] == "--worker"
        assert prefix[-2].endswith("desktop_qt_app.py")


class TestFirstImage:
    def test_missing_directory_returns_none(self, tmp_path: Path) -> None:
        assert first_image(tmp_path / "nope") is None

    def test_picks_first_by_name(self, tmp_path: Path) -> None:
        (tmp_path / "b.jpg").write_bytes(b"x")
        (tmp_path / "a.png").write_bytes(b"x")
        (tmp_path / "notes.txt").write_text("skip")
        assert first_image(tmp_path) == tmp_path / "a.png"

    def test_ignores_non_image_files(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("skip")
        assert first_image(tmp_path) is None


class TestFirstOutputImage:
    def test_prefers_contact_sheet(self, tmp_path: Path) -> None:
        review = tmp_path / "review"
        review.mkdir()
        (review / "contact_sheet.jpg").write_bytes(b"x")
        (tmp_path / "a.jpg").write_bytes(b"x")
        assert first_output_image(tmp_path) == review / "contact_sheet.jpg"

    def test_falls_back_to_direct_image(self, tmp_path: Path) -> None:
        (tmp_path / "a.jpg").write_bytes(b"x")
        assert first_output_image(tmp_path) == tmp_path / "a.jpg"

    def test_falls_back_to_review_dir(self, tmp_path: Path) -> None:
        review = tmp_path / "review"
        review.mkdir()
        (review / "candidate.jpg").write_bytes(b"x")
        assert first_output_image(tmp_path) == review / "candidate.jpg"

    def test_recursive_newest_fallback(self, tmp_path: Path) -> None:
        nested_old = tmp_path / "run1" / "deep"
        nested_new = tmp_path / "run2" / "deep"
        nested_old.mkdir(parents=True)
        nested_new.mkdir(parents=True)
        old = nested_old / "old.jpg"
        new = nested_new / "new.jpg"
        old.write_bytes(b"x")
        new.write_bytes(b"x")
        past = time.time() - 100
        os.utime(old, (past, past))
        assert first_output_image(tmp_path) == new

    def test_empty_directory_returns_none(self, tmp_path: Path) -> None:
        assert first_output_image(tmp_path) is None


class TestPathIsWithin:
    def test_direct_child(self, tmp_path: Path) -> None:
        child = tmp_path / "sub"
        assert path_is_within(str(child), [tmp_path]) is True

    def test_nested_descendant(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "c"
        assert path_is_within(str(nested), [tmp_path]) is True

    def test_root_itself(self, tmp_path: Path) -> None:
        assert path_is_within(str(tmp_path), [tmp_path]) is True

    def test_outside_root(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "definitely-elsewhere-xyz"
        assert path_is_within(str(outside), [tmp_path]) is False

    def test_empty_path(self, tmp_path: Path) -> None:
        assert path_is_within("", [tmp_path]) is False

    def test_no_roots(self, tmp_path: Path) -> None:
        assert path_is_within(str(tmp_path), []) is False


class TestResolveOutputDir:
    def test_dev_run_has_no_readonly_roots(self) -> None:
        # 非打包运行时安装目录可写，install_roots 应为空。
        assert install_roots() == []

    def test_keeps_saved_when_writable(self, tmp_path: Path) -> None:
        saved = str(tmp_path / "my-outputs")
        assert resolve_output_dir(saved, tmp_path / "base", "sub") == saved

    def test_falls_back_when_empty(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        assert resolve_output_dir("", base, "workflow_samples", "t2i") == str(
            base / "workflow_samples" / "t2i"
        )
