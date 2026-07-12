"""Unit tests for the workspace export core (deliverable discovery + move).

These cover the risky part of the workspace feature: correctly locating the
FINAL image(s) among each worker's package (which also holds prompts, requests,
status.json, super-res scratch, etc.) and moving only images into the clean
per-category workspace folder.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ui import workspace


def _img(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n")  # bytes only; discovery is by name/location
    return path


def _junk(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


class TestDiscoverDeliverables:
    def test_text_image_prefers_print_ready(self, tmp_path: Path) -> None:
        pkg = tmp_path / "20260712_120x80_background_t2i"
        _img(pkg / "master.png")  # intermediate
        _junk(pkg / "prompt.md")
        _junk(pkg / "image_generation_request.json")
        _junk(pkg / "status.json")
        _img(pkg / "_sr" / "master_realesrgan_x4.png")  # scratch, must be ignored
        deliver = _img(pkg / "print_ready" / "120乘以80_文生图背景.jpg")
        found = workspace.discover_deliverables("text-image", pkg)
        assert found == [deliver]

    def test_text_image_falls_back_to_master_without_postprocess(self, tmp_path: Path) -> None:
        pkg = tmp_path / "20260712_120x80_background_t2i"
        master = _img(pkg / "master.png")
        _junk(pkg / "prompt.md")
        _junk(pkg / "print_spec.json")
        found = workspace.discover_deliverables("text-image", pkg)
        assert found == [master]

    def test_full_poster_collects_every_candidate_print_ready(self, tmp_path: Path) -> None:
        pkg = tmp_path / "20260712_120x80_full_poster_image2"
        _junk(pkg / "status.json")
        c1 = _img(pkg / "candidate_01" / "print_ready" / "120乘以80_整图海报候选01.jpg")
        _img(pkg / "candidate_01" / "full_poster_master.jpg")  # intermediate
        _junk(pkg / "candidate_01" / "image_generation_request.json")
        c2 = _img(pkg / "candidate_02" / "print_ready" / "120乘以80_整图海报候选02.jpg")
        found = workspace.discover_deliverables("full-poster", pkg)
        assert found == [c1, c2]

    def test_full_poster_candidate_falls_back_to_master(self, tmp_path: Path) -> None:
        pkg = tmp_path / "run_full_poster_image2"
        master = _img(pkg / "candidate_01" / "full_poster_master.jpg")
        _junk(pkg / "candidate_01" / "run_full_poster_generation.sh")
        found = workspace.discover_deliverables("full-poster", pkg)
        assert found == [master]

    def test_full_poster_mixed_print_ready_and_master(self, tmp_path: Path) -> None:
        # Highest-risk branch: some candidates have print_ready, others only the
        # master fallback — each candidate resolved independently, order preserved.
        pkg = tmp_path / "run_full_poster_image2"
        c1 = _img(pkg / "candidate_01" / "print_ready" / "120乘以80_整图海报候选01.jpg")
        _img(pkg / "candidate_01" / "full_poster_master.jpg")  # ignored (print_ready wins)
        c2 = _img(pkg / "candidate_02" / "full_poster_master.jpg")  # no print_ready
        found = workspace.discover_deliverables("full-poster", pkg)
        assert found == [c1, c2]

    def test_text_image_falls_back_to_poster_master(self, tmp_path: Path) -> None:
        pkg = tmp_path / "20260712_120x80_poster_t2i"
        master = _img(pkg / "poster_master.png")
        _junk(pkg / "poster_copy.json")
        found = workspace.discover_deliverables("text-image", pkg)
        assert found == [master]

    def test_gpt_generate_master_recognized(self, tmp_path: Path) -> None:
        pkg = tmp_path / "13131_generate"
        master = _img(pkg / "gpt_image_master.png")
        found = workspace.discover_deliverables("gpt", pkg)
        assert found == [master]

    def test_unknown_worker_discovers_nothing(self, tmp_path: Path) -> None:
        _img(tmp_path / "x.png")
        assert workspace.discover_deliverables("bogus", tmp_path) == []


class TestEffectiveOutputDir:
    def test_active_uses_engineering_default(self, monkeypatch) -> None:
        monkeypatch.setattr(workspace, "is_active", lambda: True)
        assert workspace.effective_output_dir("ENG", "USER") == "ENG"

    def test_inactive_uses_field_value(self, monkeypatch) -> None:
        monkeypatch.setattr(workspace, "is_active", lambda: False)
        assert workspace.effective_output_dir("ENG", "USER") == "USER"


class _StubField:
    def __init__(self) -> None:
        self.visible = True
        self.text_calls: list[str] = []

    def setVisible(self, value: bool) -> None:
        self.visible = value

    def setText(self, value: str) -> None:
        self.text_calls.append(value)


class _StubNote:
    def __init__(self) -> None:
        self.visible = True

    def setVisible(self, value: bool) -> None:
        self.visible = value


class TestApplyOutputField:
    def test_active_hides_field_shows_note_and_never_mutates_text(self, monkeypatch) -> None:
        monkeypatch.setattr(workspace, "is_active", lambda: True)
        field, note = _StubField(), _StubNote()
        workspace.apply_output_field(field, note)
        assert field.visible is False and note.visible is True
        assert field.text_calls == []  # user's own output value is left intact

    def test_inactive_shows_field_hides_note(self, monkeypatch) -> None:
        monkeypatch.setattr(workspace, "is_active", lambda: False)
        field, note = _StubField(), _StubNote()
        workspace.apply_output_field(field, note)
        assert field.visible is True and note.visible is False

    def test_gpt_returns_only_the_master(self, tmp_path: Path) -> None:
        pkg = tmp_path / "13131_edit"
        master = _img(pkg / "gpt_image_edit_master.png")
        _img(pkg / "source_preview.jpg")  # downscaled input, NOT a deliverable
        _img(pkg / "13131.jpg")  # copy of the original source, NOT a deliverable
        _junk(pkg / "prompt.md")
        _junk(pkg / "image_edit_request.json")
        found = workspace.discover_deliverables("gpt", pkg)
        assert found == [master]

    def test_qr_returns_no_qr_file_excluding_review(self, tmp_path: Path) -> None:
        root = tmp_path / "single_no_qr_desktop_qt"
        deliver = _img(root / "海报1_no_qr.png")
        _img(root / "review" / "海报1_no_qr.jpg")  # preview, excluded
        found = workspace.discover_deliverables("qr", root)
        assert found == [deliver]

    def test_batch_returns_top_level_images_only(self, tmp_path: Path) -> None:
        root = tmp_path / "print_ready_desktop_qt"
        a = _img(root / "200乘以80_海报A.jpg")
        b = _img(root / "200乘以80_海报B.jpg")
        _img(root / "review" / "contact_sheet.jpg")  # subdir preview, excluded
        _img(root / "_masters" / "海报A_realesrgan_x4.png")  # scratch, excluded
        _junk(root / "print_audit.csv")
        found = workspace.discover_deliverables("batch-style", root)
        assert sorted(found) == sorted([a, b])

    def test_missing_root_returns_empty(self, tmp_path: Path) -> None:
        assert workspace.discover_deliverables("qr", tmp_path / "nope") == []


class TestResolveOpenTarget:
    def test_no_workspace_returns_last_output_unchanged(self, tmp_path: Path) -> None:
        eng = tmp_path / "cache" / "text_to_image_print_qt"
        eng.mkdir(parents=True)
        assert workspace.resolve_open_target("", eng) == eng

    def test_no_workspace_none_stays_none(self) -> None:
        assert workspace.resolve_open_target("", None) is None

    def test_workspace_keeps_last_output_when_inside_and_exists(self, tmp_path: Path) -> None:
        ws = tmp_path / "工作区"
        category = ws / "文生图"
        category.mkdir(parents=True)
        assert workspace.resolve_open_target(str(ws), category) == category

    def test_workspace_redirects_engineering_path_to_root(self, tmp_path: Path) -> None:
        # The reported bug: last_output_dir points at the hidden cache → open the
        # workspace, never the engineering directory.
        ws = tmp_path / "工作区"
        ws.mkdir()
        eng = tmp_path / "cache" / "text_to_image_print_qt"
        eng.mkdir(parents=True)
        assert workspace.resolve_open_target(str(ws), eng) == ws

    def test_workspace_none_last_output_returns_root(self, tmp_path: Path) -> None:
        ws = tmp_path / "工作区"
        ws.mkdir()
        assert workspace.resolve_open_target(str(ws), None) == ws

    def test_workspace_category_not_yet_created_falls_back_to_root(self, tmp_path: Path) -> None:
        ws = tmp_path / "工作区"
        ws.mkdir()
        missing_category = ws / "整图海报"  # not created (run produced nothing)
        assert workspace.resolve_open_target(str(ws), missing_category) == ws


class TestNormalizeSearchRoot:
    def test_file_done_label_resolves_to_parent(self, tmp_path: Path) -> None:
        # qr reports the output FILE, not a directory.
        out = tmp_path / "single_no_qr_desktop_qt"
        file = _img(out / "海报1_no_qr.png")
        assert workspace.normalize_search_root(str(file), tmp_path) == out

    def test_dir_done_label_kept(self, tmp_path: Path) -> None:
        pkg = tmp_path / "20260712_full_poster_image2"
        pkg.mkdir()
        assert workspace.normalize_search_root(str(pkg), tmp_path) == pkg

    def test_empty_done_label_uses_fallback(self, tmp_path: Path) -> None:
        assert workspace.normalize_search_root("", tmp_path) == tmp_path


class TestExportRun:
    def test_moves_images_into_category_folder(self, tmp_path: Path) -> None:
        ws = tmp_path / "工作区"
        pkg = tmp_path / "single_no_qr_desktop_qt"
        src = _img(pkg / "海报1_no_qr.png")
        moved = workspace.export_run("qr", pkg, ws)
        assert len(moved) == 1
        assert moved[0] == ws / "去二维码" / "海报1_no_qr.png"
        assert moved[0].exists()
        assert not src.exists()  # MOVED, not copied

    def test_gpt_master_renamed_friendly(self, tmp_path: Path) -> None:
        ws = tmp_path / "工作区"
        pkg = tmp_path / "13131_edit"
        _img(pkg / "gpt_image_edit_master.png")
        moved = workspace.export_run("gpt", pkg, ws)
        assert moved == [ws / "图片修改" / "13131_已修改.png"]

    def test_collision_gets_numeric_suffix(self, tmp_path: Path) -> None:
        ws = tmp_path / "工作区"
        (ws / "去二维码").mkdir(parents=True)
        (ws / "去二维码" / "海报1_no_qr.png").write_bytes(b"old")
        pkg = tmp_path / "single_no_qr_desktop_qt"
        _img(pkg / "海报1_no_qr.png")
        moved = workspace.export_run("qr", pkg, ws)
        assert moved == [ws / "去二维码" / "海报1_no_qr (2).png"]
        assert (ws / "去二维码" / "海报1_no_qr.png").read_bytes() == b"old"  # original kept

    def test_no_deliverables_returns_empty(self, tmp_path: Path) -> None:
        ws = tmp_path / "工作区"
        pkg = tmp_path / "empty_edit"
        pkg.mkdir()
        assert workspace.export_run("gpt", pkg, ws) == []

    def test_gpt_generate_renamed(self, tmp_path: Path) -> None:
        ws = tmp_path / "工作区"
        pkg = tmp_path / "13131_generate"
        _img(pkg / "gpt_image_master.png")
        assert workspace.export_run("gpt", pkg, ws) == [ws / "图片修改" / "13131_已修改.png"]

    def test_batch_pil_moves_every_top_level_image(self, tmp_path: Path) -> None:
        ws = tmp_path / "工作区"
        out = tmp_path / "print_ready_desktop_qt"
        _img(out / "海报A.jpg")
        _img(out / "海报B.jpg")
        moved = workspace.export_run("batch-pil", out, ws)
        assert sorted(p.name for p in moved) == ["海报A.jpg", "海报B.jpg"]
        assert (ws / "批量印刷" / "海报A.jpg").exists()

    def test_min_mtime_skips_leftovers_from_earlier_runs(self, tmp_path: Path) -> None:
        # qr/batch write to a fixed reused dir; only THIS run's outputs must move.
        ws = tmp_path / "工作区"
        out = tmp_path / "single_no_qr_desktop_qt"
        old = _img(out / "旧图_no_qr.png")
        new = _img(out / "新图_no_qr.png")
        past = time.time() - 1000
        os.utime(old, (past, past))
        moved = workspace.export_run("qr", out, ws, min_mtime=time.time() - 100)
        assert moved == [ws / "去二维码" / "新图_no_qr.png"]
        assert old.exists()  # earlier run's deliverable is left where the user saw it

    def test_partial_move_failure_keeps_going(self, tmp_path: Path, monkeypatch) -> None:
        ws = tmp_path / "工作区"
        out = tmp_path / "print_ready_desktop_qt"
        a = _img(out / "海报A.jpg")
        _img(out / "海报B.jpg")
        real_move = workspace.shutil.move

        def _flaky(src, dst):  # fail only on 海报A, succeed on 海报B
            if Path(src).name == "海报A.jpg":
                raise OSError("disk full")
            return real_move(src, dst)

        monkeypatch.setattr(workspace.shutil, "move", _flaky)
        moved = workspace.export_run("batch-pil", out, ws)
        assert [p.name for p in moved] == ["海报B.jpg"]  # partial success reported
        assert a.exists()  # the failed one stays in the package, not lost

    def test_unknown_worker_leaves_package_untouched(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        src = _img(pkg / "x.png")
        assert workspace.export_run("bogus", pkg, tmp_path / "ws") == []
        assert src.exists()

    def test_three_way_collision_suffix(self, tmp_path: Path) -> None:
        ws = tmp_path / "工作区"
        (ws / "去二维码").mkdir(parents=True)
        (ws / "去二维码" / "海报1_no_qr.png").write_bytes(b"1")
        (ws / "去二维码" / "海报1_no_qr (2).png").write_bytes(b"2")
        out = tmp_path / "single_no_qr_desktop_qt"
        _img(out / "海报1_no_qr.png")
        moved = workspace.export_run("qr", out, ws)
        assert moved == [ws / "去二维码" / "海报1_no_qr (3).png"]
