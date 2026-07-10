"""Pydantic request/response models for the baseline REST API.

Kept 3.9-compatible (``Optional``/``List``/``Dict`` rather than ``X | None``).
Baseline documents themselves are passed as free-form ``Dict[str, Any]`` — the
authoritative shape is enforced server-side by the JSON-schema validator, not by
Pydantic — so the contract never drifts from ``docs/baseline/baseline.schema.json``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ProjectInfoOut(BaseModel):
    baseline_id: str
    name: str
    active_version: Optional[str] = None
    versions: List[str] = Field(default_factory=list)


class VersionSummaryOut(BaseModel):
    version: str
    status: str


class CreateDraftOut(BaseModel):
    version: str
    etag: str


class DocumentOut(BaseModel):
    document_id: str
    url: str


class MergeJobCreateOut(BaseModel):
    job_id: str


class MergeJobStatusOut(BaseModel):
    status: str
    report: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class SetActiveIn(BaseModel):
    version: str


class MergeJobIn(BaseModel):
    document_id: Optional[str] = None
    # Optional inline extracted text: lets the client keep originals local and
    # send only text, per the Phase B privacy note.
    text: Optional[str] = None
    filename: Optional[str] = None
    model: Optional[str] = None


class ErrorOut(BaseModel):
    code: str
    messages: List[str] = Field(default_factory=list)


class AppConfigModel(BaseModel):
    """Shared client bootstrap config pushed by the admin, fetched by all clients."""

    # Optional override for where baselines live; empty = the bootstrap endpoint
    # the client is baked with (this same server, for the standard deployment).
    baseline_endpoint: str = ""
    image_api_base_url: str = ""
    image_api_key: str = ""
    baseline_model: str = "gpt-4o"
    # Where clients fetch update-manifest.json (the VPS mirror, reachable where
    # GitHub is not); empty = fall back to the URL baked at build time.
    update_manifest_url: str = ""


class AdminVerifyOut(BaseModel):
    ok: bool


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str
