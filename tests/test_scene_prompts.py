"""Unit tests for the built-in scene-prompt preset loader."""

from __future__ import annotations

import json

from ui.scene_prompts import ScenePrompt, load_scene_prompts


class TestBundledLibrary:
    def test_loads_nonempty(self) -> None:
        scenes = load_scene_prompts()
        assert scenes, "内置场景提示词库不应为空"
        assert all(isinstance(s, ScenePrompt) for s in scenes)

    def test_every_scene_has_required_fields(self) -> None:
        for scene in load_scene_prompts():
            assert scene.id and scene.label and scene.prompt

    def test_ids_are_unique(self) -> None:
        ids = [s.id for s in load_scene_prompts()]
        assert len(ids) == len(set(ids))

    def test_prompts_are_chinese(self) -> None:
        # 用户看到的是中文；每条正文都应含中文字符。
        for scene in load_scene_prompts():
            assert any("一" <= ch <= "鿿" for ch in scene.prompt)


class TestLoaderRobustness:
    def test_missing_file_returns_empty(self, tmp_path) -> None:
        assert load_scene_prompts(tmp_path / "nope.json") == []

    def test_invalid_json_returns_empty(self, tmp_path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{ not valid json", encoding="utf-8")
        assert load_scene_prompts(path) == []

    def test_non_list_scenes_returns_empty(self, tmp_path) -> None:
        path = tmp_path / "x.json"
        path.write_text(json.dumps({"scenes": "nope"}), encoding="utf-8")
        assert load_scene_prompts(path) == []

    def test_skips_incomplete_and_duplicate_entries(self, tmp_path) -> None:
        path = tmp_path / "x.json"
        path.write_text(
            json.dumps(
                {
                    "scenes": [
                        {"id": "a", "label": "A", "prompt": "中文提示"},
                        {"id": "a", "label": "dup", "prompt": "重复 id 跳过"},
                        {"id": "b", "label": "", "prompt": "缺 label 跳过"},
                        {"id": "", "label": "C", "prompt": "缺 id 跳过"},
                        {"nope": 1},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        scenes = load_scene_prompts(path)
        assert [s.id for s in scenes] == ["a"]
