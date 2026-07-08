"""B->C governance checks reused by validation, merge review, and publish.

These mirror the runtime gate in scripts/*.py (blocked_terms_in_prompt) plus
the claims_policy rules, so a baseline can be screened before it ever reaches
the image API. They never mutate the baseline — callers decide what to do.
"""

from __future__ import annotations

from typing import Any, Dict, List

# 会真正进入 C 端出图的层：只在这些层里出现禁用词/违规主张才算硬违规
# （source_facts.business_terms 允许保留原始 B 端措辞，仅供溯源）。


def _iter_strings(value: Any) -> "list[str]":
    out: List[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_iter_strings(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_iter_strings(v))
    return out


# consumer_baseline 里这两项是"声明"而非出图文案：blocked_keywords 是黑名单
# 本身、claims_policy 会逐字列出被禁止的主张，扫描它们会自我误报。
_CONSUMER_DECL_FIELDS = ("blocked_keywords", "claims_policy")


def _consumer_haystack(baseline: Dict[str, Any]) -> str:
    # 只扫 consumer_baseline 的"正向文案"字段。visual_guidelines.avoid_scenes 与
    # prompt_policy.negative_constraints 是"避免"指令，会合法地点名被禁概念，
    # 扫它们会误报（与脚本运行时只校验正向 prompt/文案一致）。
    consumer = dict(baseline.get("consumer_baseline", {}) or {})
    for field in _CONSUMER_DECL_FIELDS:
        consumer.pop(field, None)
    return "\n".join(_iter_strings(consumer))


def blocked_keyword_hits(baseline: Dict[str, Any]) -> "list[str]":
    """Blocked keywords found in the consumer-facing copy (should be empty)."""
    consumer = baseline.get("consumer_baseline", {}) or {}
    blocked = [str(k).strip() for k in consumer.get("blocked_keywords", []) if str(k).strip()]
    if not blocked:
        return []
    haystack = _consumer_haystack(baseline)
    hits: List[str] = []
    for term in blocked:
        if term and term in haystack and term not in hits:
            hits.append(term)
    return hits


def forbidden_claim_terms(baseline: Dict[str, Any]) -> "list[str]":
    consumer = baseline.get("consumer_baseline", {}) or {}
    policy = consumer.get("claims_policy", {}) or {}
    return [str(c).strip() for c in policy.get("forbidden", []) if str(c).strip()]


def governance_issues(baseline: Dict[str, Any]) -> "list[str]":
    """Human-readable governance problems (empty == clean)."""
    issues: List[str] = []
    for hit in blocked_keyword_hits(baseline):
        issues.append(f"C 端层出现禁用词：{hit}")
    return issues
