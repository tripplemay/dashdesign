"""B->C governance checks reused by validation, merge review, and publish.

Enforces two iron rules over the content that actually reaches to-C image
generation:
  1. blocked_keywords must not appear in consumer-facing copy (B-side leak);
  2. claims_policy.forbidden (guarantees of skill mastery / school-advancement /
     income) must not appear — detected via promise-verb triggers.

The scanned "haystack" is the positive prompt-bearing content only: the
consumer_baseline copy plus the *positive* visual_guidelines / prompt_policy
fields that are injected into the prompt. The avoid/negative directives
(avoid_scenes, negative_constraints) legitimately name forbidden concepts and
are excluded, matching the scripts' runtime prompt gate.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Dict, List

# consumer_baseline 里这两项是"声明"而非出图文案，扫描会自我误报。
_CONSUMER_DECL_FIELDS = ("blocked_keywords", "claims_policy")
# visual_guidelines / prompt_policy 里"避免/负向"指令合法地点名被禁概念，排除之。
_VISUAL_AVOID_FIELDS = ("avoid_scenes",)
_PROMPT_NEGATIVE_FIELDS = ("negative_constraints",)

# 承诺型触发词：出现即视为对家长/学生做出保证（升学/技能/收益/结果）。
PROMISE_TRIGGERS = (
    "保证", "承诺", "确保", "一定能", "一定可以", "100%", "百分百",
    "包过", "包升学", "包就业", "包会", "稳赚", "必然", "绝对",
)


def _norm(text: str) -> str:
    # NFKC 折叠全/半角，casefold 归一大小写，抵御 ASCII/宽度/大小写变体绕过。
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


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


def consumer_prompt_texts(baseline: Dict[str, Any]) -> "list[str]":
    """所有会进入 to-C 出图 prompt 的正向文案（不含避免/负向指令与声明）。"""
    parts: List[str] = []
    consumer = dict(baseline.get("consumer_baseline", {}) or {})
    for field in _CONSUMER_DECL_FIELDS:
        consumer.pop(field, None)
    parts.extend(_iter_strings(consumer))
    visual = dict(baseline.get("visual_guidelines", {}) or {})
    for field in _VISUAL_AVOID_FIELDS:
        visual.pop(field, None)
    parts.extend(_iter_strings(visual))
    prompt_policy = dict(baseline.get("prompt_policy", {}) or {})
    for field in _PROMPT_NEGATIVE_FIELDS:
        prompt_policy.pop(field, None)
    parts.extend(_iter_strings(prompt_policy))
    return parts


def _haystack(baseline: Dict[str, Any]) -> str:
    return "\n".join(_norm(p) for p in consumer_prompt_texts(baseline))


def blocked_keyword_hits(baseline: Dict[str, Any]) -> "list[str]":
    """Blocked keywords found in the prompt-bearing consumer content."""
    consumer = baseline.get("consumer_baseline", {}) or {}
    blocked = [str(k).strip() for k in consumer.get("blocked_keywords", []) if str(k).strip()]
    if not blocked:
        return []
    haystack = _haystack(baseline)
    hits: List[str] = []
    for term in blocked:
        if _norm(term) and _norm(term) in haystack and term not in hits:
            hits.append(term)
    return hits


def forbidden_claim_hits(baseline: Dict[str, Any]) -> "list[str]":
    """Promise-verb triggers found in prompt-bearing content (guarantee claims)."""
    haystack = _haystack(baseline)
    hits: List[str] = []
    for trigger in PROMISE_TRIGGERS:
        if _norm(trigger) in haystack and trigger not in hits:
            hits.append(trigger)
    return hits


def governance_issues(baseline: Dict[str, Any]) -> "list[str]":
    """Human-readable governance problems (empty == clean)."""
    issues: List[str] = []
    for hit in blocked_keyword_hits(baseline):
        issues.append(f"C 端文案出现禁用词：{hit}")
    for hit in forbidden_claim_hits(baseline):
        issues.append(f"C 端文案疑似做出承诺（含“{hit}”），违反 claims_policy 禁止承诺升学/技能/收益")
    return issues
