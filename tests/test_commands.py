"""Unit tests for the pure command builders."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app_runtime import baseline_path, prompt_template_library_path
from ui.commands import (
    BatchForm,
    GptForm,
    QrForm,
    TextImageForm,
    api_env,
    build_batch_command,
    build_gpt_command,
    build_qr_command,
    build_text_image_command,
)


def text_image_form(**overrides: object) -> TextImageForm:
    base = TextImageForm(
        output_dir="/tmp/out",
        prompt="明亮教室",
        mode="background",
        poster_copy="",
        width_cm="120",
        height_cm="80",
        dpi="200",
        candidates="4",
        full_style="",
        purpose_template="course_enrollment",
        style_template="tech_neon",
        layout_template="headline_modules_cta",
        text_density="medium",
        image_size="auto",
        quality="high",
        execute=False,
        postprocess=True,
        base_url="",
        api_key="",
    )
    return dataclasses.replace(base, **overrides)


class TestApiEnv:
    def test_empty_inputs_produce_empty_env(self) -> None:
        assert api_env("", "") == {}
        assert api_env("  ", "  ") == {}

    def test_values_are_stripped(self) -> None:
        env = api_env(" https://gw.example/v1 ", " sk-abc ")
        assert env == {
            "OPENAI_BASE_URL": "https://gw.example/v1",
            "OPENAI_API_KEY": "sk-abc",
        }


class TestTextImageCommand:
    def test_background_mode_basic_command(self) -> None:
        command, output_dir, env = build_text_image_command(text_image_form())
        assert "--worker" in command
        assert "text-image" in command
        assert "--baseline" in command
        assert command[command.index("--baseline") + 1] == str(baseline_path())
        assert "--mode" in command
        assert command[command.index("--mode") + 1] == "background"
        assert "--postprocess-print" in command
        assert "--execute" not in command
        assert output_dir == Path("/tmp/out")
        assert env == {}

    def test_background_mode_requires_prompt(self) -> None:
        with pytest.raises(ValueError, match="提示词"):
            build_text_image_command(text_image_form(prompt="  "))

    def test_deprecated_poster_mode_rejected(self) -> None:
        # 带文字海报（本地合成）已废弃并从工具移除
        with pytest.raises(ValueError, match="未知输出类型"):
            build_text_image_command(text_image_form(mode="poster"))

    def test_full_poster_allows_empty_prompt(self) -> None:
        command, _, _ = build_text_image_command(
            text_image_form(mode="full_poster", prompt="", poster_copy="主标题")
        )
        assert "full-poster" in command
        assert "--template-library" in command
        assert command[command.index("--template-library") + 1] == str(prompt_template_library_path())
        assert "--negative-template" in command
        assert "--candidates" in command
        assert command[command.index("--candidates") + 1] == "4"

    def test_full_poster_requires_copy(self) -> None:
        with pytest.raises(ValueError, match="海报文案"):
            build_text_image_command(text_image_form(mode="full_poster", prompt=""))

    def test_non_numeric_dimensions_rejected(self) -> None:
        with pytest.raises(ValueError, match="必须是数字"):
            build_text_image_command(text_image_form(width_cm="abc"))

    def test_non_positive_dimensions_rejected(self) -> None:
        with pytest.raises(ValueError, match="大于 0"):
            build_text_image_command(text_image_form(dpi="0"))

    def test_invalid_candidates_rejected(self) -> None:
        with pytest.raises(ValueError, match="候选数"):
            build_text_image_command(text_image_form(candidates="x"))
        with pytest.raises(ValueError, match="候选数"):
            build_text_image_command(text_image_form(candidates="0"))

    def test_empty_candidates_defaults_to_one(self) -> None:
        command, _, _ = build_text_image_command(text_image_form(candidates=""))
        assert "text-image" in command

    def test_execute_flag_and_env(self) -> None:
        command, _, env = build_text_image_command(
            text_image_form(execute=True, base_url="https://gw/v1", api_key="sk-1")
        )
        assert "--execute" in command
        assert env == {"OPENAI_BASE_URL": "https://gw/v1", "OPENAI_API_KEY": "sk-1"}

    def test_poster_copy_is_forwarded(self) -> None:
        command, _, _ = build_text_image_command(
            text_image_form(mode="full_poster", poster_copy="主标题\n副标题")
        )
        assert "--poster-copy" in command
        assert command[command.index("--poster-copy") + 1] == "主标题\n副标题"

    def test_background_mode_has_no_text_style(self) -> None:
        command, _, _ = build_text_image_command(text_image_form())
        assert "--text-style" not in command


class TestBatchCommand:
    def batch_form(self, tmp_path: Path, **overrides: object) -> BatchForm:
        base = BatchForm(
            input_dir=str(tmp_path),
            output_dir=str(tmp_path / "out"),
            style_mode=True,
            dpi="200",
            only="",
            force=True,
            keep_masters=False,
        )
        return dataclasses.replace(base, **overrides)

    def test_style_mode_command(self, tmp_path: Path) -> None:
        command, output_dir, env = build_batch_command(self.batch_form(tmp_path))
        assert "batch-style" in command
        assert "--realesrgan-binary" in command
        assert "--realesrgan-model-dir" in command
        assert "--force" in command
        assert "--keep-masters" not in command
        assert output_dir == tmp_path / "out"
        assert env == {}

    def test_pil_mode_command(self, tmp_path: Path) -> None:
        command, _, _ = build_batch_command(self.batch_form(tmp_path, style_mode=False, force=False))
        assert "batch-pil" in command
        assert "--realesrgan-binary" not in command
        assert "--force" not in command

    def test_missing_input_dir_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="输入目录不存在"):
            build_batch_command(self.batch_form(tmp_path, input_dir=str(tmp_path / "nope")))

    def test_empty_input_dir_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="请选择输入目录"):
            build_batch_command(self.batch_form(tmp_path, input_dir="  "))

    def test_empty_dpi_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="DPI"):
            build_batch_command(self.batch_form(tmp_path, dpi="  "))

    def test_only_filter_forwarded(self, tmp_path: Path) -> None:
        command, _, _ = build_batch_command(self.batch_form(tmp_path, only="海报.jpg"))
        assert "--only" in command
        assert command[command.index("--only") + 1] == "海报.jpg"


class TestGptCommand:
    def gpt_form(self, source: Path, **overrides: object) -> GptForm:
        base = GptForm(
            source=str(source),
            output_dir="/tmp/gpt-out",
            mode="edit",
            dpi="200",
            description="",
            execute=False,
            base_url="",
            api_key="",
        )
        return dataclasses.replace(base, **overrides)

    def test_basic_command(self, tmp_path: Path) -> None:
        source = tmp_path / "poster.jpg"
        source.write_bytes(b"fake")
        command, output_dir, env = build_gpt_command(self.gpt_form(source))
        assert "gpt" in command
        assert str(source) in command
        assert command[command.index("--api-mode") + 1] == "edit"
        assert "--execute" not in command
        assert output_dir == Path("/tmp/gpt-out")
        assert env == {}

    def test_missing_source_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="源图片不存在"):
            build_gpt_command(self.gpt_form(tmp_path / "missing.jpg"))

    def test_description_execute_and_env(self, tmp_path: Path) -> None:
        source = tmp_path / "poster.jpg"
        source.write_bytes(b"fake")
        command, _, env = build_gpt_command(
            self.gpt_form(source, description="重绘背景", execute=True, api_key="sk-2")
        )
        assert "--description" in command
        assert command[command.index("--description") + 1] == "重绘背景"
        assert "--execute" in command
        assert env == {"OPENAI_API_KEY": "sk-2"}


class TestQrCommand:
    def qr_form(self, source: Path, **overrides: object) -> QrForm:
        base = QrForm(
            source=str(source),
            output_dir="/tmp/qr-out",
            box="100,200,300,400",
            reference_size="",
            margin="0.55",
            radius="21",
        )
        return dataclasses.replace(base, **overrides)

    def test_basic_command(self, tmp_path: Path) -> None:
        source = tmp_path / "poster.jpg"
        source.write_bytes(b"fake")
        command, output_dir, env = build_qr_command(self.qr_form(source))
        assert "qr" in command
        assert command[command.index("--box") + 1] == "100,200,300,400"
        assert command[command.index("--margin-ratio") + 1] == "0.55"
        assert command[command.index("--inpaint-radius") + 1] == "21"
        assert "--reference-size" not in command
        assert output_dir == Path("/tmp/qr-out")
        assert env == {}

    def test_missing_source_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="输入图片不存在"):
            build_qr_command(self.qr_form(tmp_path / "missing.jpg"))

    def test_empty_box_rejected(self, tmp_path: Path) -> None:
        source = tmp_path / "poster.jpg"
        source.write_bytes(b"fake")
        with pytest.raises(ValueError, match="清除区域"):
            build_qr_command(self.qr_form(source, box=" "))

    def test_malformed_box_rejected(self, tmp_path: Path) -> None:
        source = tmp_path / "poster.jpg"
        source.write_bytes(b"fake")
        with pytest.raises(ValueError, match="x1,y1,x2,y2"):
            build_qr_command(self.qr_form(source, box="a,b,c,d"))
        with pytest.raises(ValueError, match="x1,y1,x2,y2"):
            build_qr_command(self.qr_form(source, box="1,2,3"))

    def test_inverted_box_rejected(self, tmp_path: Path) -> None:
        source = tmp_path / "poster.jpg"
        source.write_bytes(b"fake")
        with pytest.raises(ValueError, match="x2 > x1"):
            build_qr_command(self.qr_form(source, box="300,200,100,400"))

    def test_malformed_reference_size_rejected(self, tmp_path: Path) -> None:
        source = tmp_path / "poster.jpg"
        source.write_bytes(b"fake")
        with pytest.raises(ValueError, match="参考尺寸"):
            build_qr_command(self.qr_form(source, reference_size="宽1295"))

    def test_box_with_spaces_normalized(self, tmp_path: Path) -> None:
        source = tmp_path / "poster.jpg"
        source.write_bytes(b"fake")
        command, _, _ = build_qr_command(self.qr_form(source, box=" 100, 200, 300, 400 "))
        assert command[command.index("--box") + 1] == "100,200,300,400"

    def test_fullwidth_comma_and_decimals_accepted(self, tmp_path: Path) -> None:
        # 与 scripts/remove_qr_area.py 的 parse_box 语义一致
        source = tmp_path / "poster.jpg"
        source.write_bytes(b"fake")
        command, _, _ = build_qr_command(self.qr_form(source, box="100，200，300，400"))
        assert command[command.index("--box") + 1] == "100,200,300,400"
        command, _, _ = build_qr_command(self.qr_form(source, box="100.5,200,300.9,400"))
        assert command[command.index("--box") + 1] == "100,200,300,400"

    def test_reference_size_forwarded(self, tmp_path: Path) -> None:
        source = tmp_path / "poster.jpg"
        source.write_bytes(b"fake")
        command, _, _ = build_qr_command(self.qr_form(source, reference_size="3238x1295"))
        assert command[command.index("--reference-size") + 1] == "3238x1295"
