"""Unit tests for physical-size resolution in the GPT image rebuild worker.

Regression coverage for chaining text-to-image / full-poster output
(``master.png``, whose filename carries no size) into the GPT rebuild worker.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

# scripts/ is not a package and not on the default test path; the worker adds it
# at runtime (runpy from the scripts dir). Mirror that here to import the module.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import gpt_image_rebuild as rebuild  # noqa: E402


def _write_print_spec(directory: Path, width_cm: float, height_cm: float) -> None:
    (directory / "print_spec.json").write_text(
        json.dumps({"width_cm": width_cm, "height_cm": height_cm, "dpi": 200}),
        encoding="utf-8",
    )


def _write_png(path: Path, size: tuple[int, int] = (64, 64)) -> None:
    # build_profile opens the source and samples its palette, so it needs a
    # real raster (unlike resolve_physical_size, which only reads the path).
    Image.new("RGB", size, (120, 130, 140)).save(path)


class TestResolvePhysicalSize:
    def test_size_token_in_filename_wins(self, tmp_path: Path) -> None:
        source = tmp_path / "美业海报_80乘80.png"
        source.touch()
        assert rebuild.resolve_physical_size(source) == (80, 80)

    def test_falls_back_to_sibling_print_spec(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        source.touch()
        _write_print_spec(tmp_path, 80.0, 80.0)
        # 80.0 in the spec is normalized to the int 80 to match filename parsing.
        assert rebuild.resolve_physical_size(source) == (80, 80)

    def test_print_spec_preferred_over_directory_name(self, tmp_path: Path) -> None:
        workdir = tmp_path / "20260708_184744_60x90_background_t2i"
        workdir.mkdir()
        source = workdir / "master.png"
        source.touch()
        _write_print_spec(workdir, 80.0, 80.0)
        assert rebuild.resolve_physical_size(source) == (80, 80)

    def test_falls_back_to_ancestor_directory_name(self, tmp_path: Path) -> None:
        workdir = tmp_path / "20260708_184744_80x80_background_t2i"
        workdir.mkdir()
        source = workdir / "master.png"
        source.touch()
        assert rebuild.resolve_physical_size(source) == (80, 80)

    def test_non_integer_cm_preserved(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        source.touch()
        _write_print_spec(tmp_path, 21.0, 29.7)
        assert rebuild.resolve_physical_size(source) == (21, 29.7)

    def test_malformed_print_spec_ignored(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        source.touch()
        (tmp_path / "print_spec.json").write_text("{not json", encoding="utf-8")
        assert rebuild.resolve_physical_size(source) is None

    def test_non_positive_size_ignored(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        source.touch()
        _write_print_spec(tmp_path, 0, 80)
        assert rebuild.resolve_physical_size(source) is None

    def test_returns_none_when_unresolvable(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        source.touch()
        assert rebuild.resolve_physical_size(source) is None


class TestBuildProfileError:
    def test_raises_clear_error_when_size_unresolvable(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        source.touch()
        with pytest.raises(ValueError, match="无法确定物理尺寸"):
            rebuild.build_profile(source, print_dpi=200)


class TestResolveSizeOverride:
    def test_both_none_returns_none(self) -> None:
        assert rebuild.resolve_size_override(None, None) is None

    def test_partial_override_rejected(self) -> None:
        with pytest.raises(ValueError, match="同时提供"):
            rebuild.resolve_size_override(80.0, None)
        with pytest.raises(ValueError, match="同时提供"):
            rebuild.resolve_size_override(None, 80.0)

    def test_non_positive_rejected(self) -> None:
        with pytest.raises(ValueError, match="正数"):
            rebuild.resolve_size_override(0, 80)
        with pytest.raises(ValueError, match="正数"):
            rebuild.resolve_size_override(80, -5)

    def test_integers_normalized(self) -> None:
        result = rebuild.resolve_size_override(80.0, 80.0)
        # `(80.0, 80.0) == (80, 80)` is True in Python, so assert the int-ness
        # that normalization actually produces (keeps target_cm clean: "80x80").
        assert result == (80, 80)
        assert [type(value) for value in result] == [int, int]

    def test_non_integer_preserved(self) -> None:
        result = rebuild.resolve_size_override(21.0, 29.7)
        assert result == (21, 29.7)
        assert isinstance(result[0], int) and isinstance(result[1], float)


class TestBuildProfileSizeOverride:
    def test_override_wins_over_filename_token(self, tmp_path: Path) -> None:
        # Filename says 50乘50 but the explicit override must take precedence.
        source = tmp_path / "美业海报_50乘50.png"
        _write_png(source)
        profile = rebuild.build_profile(source, print_dpi=200, size_override=(80, 80))
        assert profile.target_cm == "80x80"

    def test_override_used_when_no_inference_available(self, tmp_path: Path) -> None:
        # This is the log-2 case: a bare Desktop photo with no size hint at all.
        source = tmp_path / "13131.png"
        _write_png(source)
        profile = rebuild.build_profile(source, print_dpi=200, size_override=(80, 80))
        assert profile.target_cm == "80x80"

    def test_no_override_still_infers_from_filename(self, tmp_path: Path) -> None:
        source = tmp_path / "master_80乘80.png"
        _write_png(source)
        profile = rebuild.build_profile(source, print_dpi=200)
        assert profile.target_cm == "80x80"

    def test_float_ui_size_yields_clean_target_cm(self, tmp_path: Path) -> None:
        # Full UI/CLI flow: QDoubleSpinBox 120.0 → --width-cm 120.0 → float →
        # resolve_size_override normalizes → build_profile emits "120x80", not
        # "120.0x80.0". Guards the float→"NxN" end to end (see log-2 fix).
        source = tmp_path / "13131.png"
        _write_png(source)
        size = rebuild.resolve_size_override(120.0, 80.0)
        profile = rebuild.build_profile(source, print_dpi=200, size_override=size)
        assert profile.target_cm == "120x80"


class TestMainSizeOverrideGlue:
    def test_partial_override_exits_with_argparse_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "x.png"
        _write_png(source)
        monkeypatch.setattr(
            sys, "argv", ["gpt_image_rebuild.py", str(source), "--width-cm", "80"]
        )
        with pytest.raises(SystemExit) as excinfo:
            rebuild.main()
        assert excinfo.value.code == 2

    def test_both_flags_forward_override_to_build_package(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "x.png"
        _write_png(source)
        captured: dict[str, object] = {}

        def _spy(*args: object, **_kwargs: object) -> Path:
            captured["size_override"] = args[6]  # 7th positional in build_package
            return tmp_path / "pkg"

        monkeypatch.setattr(rebuild, "build_package", _spy)
        monkeypatch.setattr(
            sys,
            "argv",
            ["gpt_image_rebuild.py", str(source), "--width-cm", "80", "--height-cm", "60"],
        )
        assert rebuild.main() == 0
        assert captured["size_override"] == (80, 60)


def _profile(gpt_image_size: str = "1024x1536") -> "rebuild.ImageProfile":
    return rebuild.ImageProfile(
        source="poster.png",
        source_px="900x1200",
        target_cm="80x100",
        print_px_at_dpi="6299x7874",
        print_dpi=200,
        gpt_image_size=gpt_image_size,
        aspect_ratio=0.8,
        dominant_palette=["#112233", "#445566", "#778899"],
        source_effective_dpi=72.0,
        notes=[],
    )


class TestEditPromptPreservesText:
    """The GUI "图片修改" path (api_mode="edit") must keep original text, not wipe it."""

    def test_edit_prompt_forbids_removing_text(self) -> None:
        prompt = rebuild.build_visual_prompt(_profile(), "把背景换成蓝天草地", "edit")
        lowered = prompt.lower()
        # The bug was the old "Text policy: remove readable ..." line; it must be gone.
        # (Note: "empty panels" still appears, but only inside the negated
        # "do NOT replace text with empty panels" preservation clause.)
        assert "remove readable" not in lowered
        # And none of the legacy rebuild framing that contradicted preservation.
        assert "rebuild the source poster" not in lowered
        assert "clean image-model master" not in lowered

    def test_edit_prompt_contains_preservation_directives(self) -> None:
        prompt = rebuild.build_visual_prompt(_profile(), "把背景换成蓝天草地", "edit")
        # Exact substrings the design settled on — keep these byte-stable.
        for phrase in (
            "PRESERVE EVERY PIECE OF TEXT EXACTLY AS IN THE SOURCE",
            "keep 100% of everything else pixel-identical to the source",
            "do NOT replace text with empty panels, glows, frames, or texture",
            "targeted, surgical edit of the existing image",
        ):
            assert phrase in prompt, f"missing preservation phrase: {phrase!r}"

    def test_edit_prompt_keeps_explicit_text_change_exception(self) -> None:
        # A user who explicitly asks to change text must still be able to.
        prompt = rebuild.build_visual_prompt(_profile(), "去掉左下角文字", "edit")
        assert "THE ONLY EXCEPTION" in prompt

    def test_edit_prompt_embeds_user_description(self) -> None:
        prompt = rebuild.build_visual_prompt(_profile(), "把背景换成蓝天草地", "edit")
        assert "把背景换成蓝天草地" in prompt

    def test_edit_prompt_blank_description_becomes_no_change(self) -> None:
        for description in (None, "", "   "):
            prompt = rebuild.build_visual_prompt(_profile(), description, "edit")
            assert rebuild._EDIT_NO_CHANGE_DESCRIPTION in prompt
            # The old generic fallback would read as an open-ended redraw instruction.
            assert "visual reference for subject, layout, mood, and color" not in prompt

    def test_edit_prompt_interpolates_size_and_palette(self) -> None:
        prompt = rebuild.build_visual_prompt(_profile("1408x1024"), "调整", "edit")
        assert "1408x1024" in prompt
        assert "#112233" in prompt


class TestGeneratePromptUnchanged:
    """Regression guard: the text-to-image / CLI rebuild path is intentionally left as-is."""

    def test_generate_prompt_still_removes_text(self) -> None:
        prompt = rebuild.build_visual_prompt(_profile(), None, "generate")
        assert "remove readable Chinese and English text" in prompt
        assert "Rebuild the source poster" in prompt

    def test_generate_prompt_has_no_preservation_directive(self) -> None:
        prompt = rebuild.build_visual_prompt(_profile(), None, "generate")
        assert "PRESERVE EVERY PIECE OF TEXT EXACTLY AS IN THE SOURCE" not in prompt
