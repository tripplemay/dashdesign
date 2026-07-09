"""Unit tests for full-baseline generation from a document (new-domain bootstrap)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app_runtime import baseline_path
from baseline import generate, governance, merge
from baseline.ingest.parse import parse_document
from baseline.schema import validation_errors

_DOC = (
    "商业皮肤学课程产品化方案。项目把看脸、追因、定案、陪跑能力，产品化成一套新美业成交系统。"
    "美肌品鉴会用于教育市场、建立专业信任。个人皮肤诊断完整梳理皮肤历史。"
    "商业皮肤学两天一夜大课面向代理，训练从卖产品升级为会诊断、会成交、会复购。"
)


@pytest.fixture(scope="module")
def template() -> dict:
    return json.loads(baseline_path().read_text(encoding="utf-8"))


def _parsed(tmp_path: Path):
    p = tmp_path / "lbx.txt"
    p.write_text(_DOC, encoding="utf-8")
    return parse_document(p)


def _fake_chat(messages, response_format=None):  # noqa: ANN001
    return json.dumps({
        "target_audience_mode": "to_b_partnership",
        "source_context_hint": "to_b_partnership_docs",
        "positioning": {"text": "把皮肤专业能力产品化为新美业成交系统",
                        "quote": "产品化成一套新美业成交系统"},
        "business_context": {"text": "面向美业代理的成交与复购体系",
                             "quote": "会诊断、会成交、会复购"},
        "modules": [
            {"name": "美肌品鉴会", "description": "教育市场、建立专业信任", "quote": "建立专业信任"},
            {"name": "个人皮肤诊断", "description": "梳理皮肤历史", "quote": "梳理皮肤历史"},
        ],
        "evidence": [
            {"id": "ev_1", "section": "第 1 页", "quote": "产品化成一套新美业成交系统"},
            {"id": "ev_2", "section": "第 1 页", "quote": "会诊断、会成交、会复购"},
        ],
        "candidates": [
            {"target": "source_facts.business_terms", "text": "两天一夜大课面向代理，训练卖产品升级为会成交会复购",
             "confidence": 0.9, "evidence": ["ev_2"]},
            {"target": "source_facts.consumer_safe_facts", "text": "美肌品鉴会帮助客户重新看懂皮肤问题",
             "confidence": 0.8, "evidence": ["ev_1"]},
        ],
    }, ensure_ascii=False)


def test_generate_valid_new_domain_baseline(tmp_path: Path, template: dict) -> None:
    parsed = _parsed(tmp_path)
    skeleton, report = generate.generate_from_document(
        parsed, template, "commercial_skin_course", "商业皮肤学", _fake_chat, "2026.07.11"
    )
    draft = generate.finalize(skeleton, report)

    # schema 合法 + 治理干净
    assert validation_errors(draft) == [], validation_errors(draft)
    assert governance.governance_issues(draft) == []
    # 全新项目：不是继承 kids 内容
    assert draft["baseline_id"] == "commercial_skin_course"
    assert draft["project"]["name"] == "商业皮肤学"
    assert draft["version"] == "2026.07.11.1"
    assert draft["parent_version"] is None
    assert draft["status"] == "draft"
    assert draft["target_audience_mode"] == "to_b_partnership"
    # 新领域内容进来了、kids 内容被清掉
    assert draft["source_documents"][0]["document_id"] == "lbx"
    module_names = [m["name"] for m in draft["source_facts"]["course_system"]]
    assert "美肌品鉴会" in module_names
    assert draft["project"]["core_positioning"]["text"] == "把皮肤专业能力产品化为新美业成交系统"
    bt = [i["text"] for i in draft["source_facts"]["business_terms"]]
    assert any("代理" in t for t in bt)
    # kids 的核心信息不应残留
    assert draft["consumer_baseline"]["core_messages"] == [] or all(
        "AI 数字化" not in i.get("text", "") for i in draft["consumer_baseline"]["core_messages"]
    )
    # 证据都接地到本文档
    assert draft["evidence_index"]
    assert all(e["document_id"] == "lbx" for e in draft["evidence_index"])


def test_generate_falls_back_when_model_empty(tmp_path: Path, template: dict) -> None:
    parsed = _parsed(tmp_path)

    def empty_chat(messages, response_format=None):  # noqa: ANN001
        return json.dumps({"target_audience_mode": "internal", "modules": [],
                           "evidence": [], "candidates": []}, ensure_ascii=False)

    skeleton, report = generate.generate_from_document(
        parsed, template, "empty_case", "空用例", empty_chat, "2026.07.11"
    )
    draft = generate.finalize(skeleton, report)
    # 即便模型什么都没给，占位也要保证 schema 合法（minItems 满足）
    assert validation_errors(draft) == [], validation_errors(draft)
    assert len(draft["source_facts"]["course_system"]) >= 1
    assert len(draft["evidence_index"]) >= 1
