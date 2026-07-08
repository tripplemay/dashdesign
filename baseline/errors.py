"""Baseline domain exceptions."""

from __future__ import annotations


class BaselineError(Exception):
    """Base class for baseline domain errors."""


class ValidationError(BaselineError):
    """A baseline failed JSON-schema validation."""

    def __init__(self, messages: "list[str]") -> None:
        self.messages = messages
        super().__init__("；".join(messages) if messages else "基线校验失败")


class GovernanceError(BaselineError):
    """A baseline violated a B->C governance rule (blocked keyword / forbidden claim)."""

    def __init__(self, messages: "list[str]") -> None:
        self.messages = messages
        super().__init__("；".join(messages) if messages else "基线违反治理规则")
