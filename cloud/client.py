"""HTTP-backed baseline repository (Phase B desktop client).

Mirrors ``baseline.store.BaselineRepository``'s surface so the GUI
(``ui/baseline_service.py``) is unchanged when the operator points the app at a
cloud endpoint. Depends only on ``requests`` (already a client dependency) — it
never imports ``cloud.server`` or any server-only package.

Path-returning methods (``version_path`` / ``active_baseline_path``) materialize
the fetched baseline JSON into a local cache dir so the CLI worker's
``--baseline <path>`` and the "open JSON" action keep working. The cache also
serves as the offline read fallback.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from baseline import versioning
from baseline.errors import BaselineError, GovernanceError, ValidationError
from baseline.store import ProjectInfo, VersionSummary, today_str

_DEFAULT_TIMEOUT = 30


def _raise_for_error(resp: requests.Response) -> None:
    if resp.status_code < 400:
        return
    code = "baseline_error"
    messages: List[str] = []
    try:
        body = resp.json()
        code = str(body.get("code", code))
        messages = [str(m) for m in body.get("messages", []) if str(m)]
    except (ValueError, AttributeError):
        messages = [resp.text[:300] or f"HTTP {resp.status_code}"]
    if not messages:
        messages = [f"HTTP {resp.status_code}"]
    if code == "validation_error":
        raise ValidationError(messages)
    if code == "governance_error":
        raise GovernanceError(messages)
    raise BaselineError("；".join(messages))


class HttpBaselineRepository:
    def __init__(self, base_url: str, token: str, cache_root: Path, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.cache_root = Path(cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"
        # Remember the ETag of the last fetched version for optimistic locking.
        self._etags: Dict[Tuple[str, str], str] = {}

    # -- HTTP helpers --------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def _get(self, path: str, **kwargs) -> requests.Response:
        try:
            return self._session.get(self._url(path), timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise BaselineError(f"云端不可达：{exc}") from exc

    def _post(self, path: str, **kwargs) -> requests.Response:
        try:
            return self._session.post(self._url(path), timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise BaselineError(f"云端不可达：{exc}") from exc

    def _put(self, path: str, **kwargs) -> requests.Response:
        try:
            return self._session.put(self._url(path), timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise BaselineError(f"云端不可达：{exc}") from exc

    # -- cache ---------------------------------------------------------
    def _cache_path(self, baseline_id: str, version: str) -> Path:
        return self.cache_root / baseline_id / "versions" / f"{version}.json"

    def _write_cache(self, baseline_id: str, version: str, data: dict) -> Path:
        path = self._cache_path(baseline_id, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 唯一临时名：两个后台线程可能并发缓存同一版本（异步页面同时拉取时会发生），
        # 固定临时名会互相踩到同一个 .tmp 上导致 FileNotFoundError 或写坏文件；各写
        # 各的 tmp 后再原子 replace 即安全（内容相同，最后一个赢）。
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    # -- projects ------------------------------------------------------
    def list_projects(self) -> List[ProjectInfo]:
        resp = self._get("/projects")
        _raise_for_error(resp)
        return [self._to_info(item) for item in resp.json()]

    def get_project(self, baseline_id: str) -> Optional[ProjectInfo]:
        # Mirror the filesystem repo: an empty/blank id is simply "no such
        # project" (None), never an HTTP call. Guards against GET /projects/
        # redirecting to the list route (an array) and crashing the GUI when no
        # project is selected on a fresh cloud backend with zero projects.
        if not str(baseline_id).strip():
            return None
        resp = self._get(f"/projects/{baseline_id}")
        if resp.status_code == 404:
            return None
        _raise_for_error(resp)
        return self._to_info(resp.json())

    def create_project(self, baseline: dict) -> ProjectInfo:
        resp = self._post("/projects", json=baseline)
        _raise_for_error(resp)
        return self._to_info(resp.json())

    # -- versions ------------------------------------------------------
    def list_versions(self, baseline_id: str) -> List[str]:
        resp = self._get(f"/projects/{baseline_id}/versions")
        _raise_for_error(resp)
        return [str(item["version"]) for item in resp.json()]

    def list_version_summaries(self, baseline_id: str) -> List[VersionSummary]:
        # The server already returns {version, status} per version, so one GET
        # yields every status — no need to download each version to read it.
        resp = self._get(f"/projects/{baseline_id}/versions")
        _raise_for_error(resp)
        return [
            VersionSummary(version=str(item["version"]), status=str(item.get("status", "draft")))
            for item in resp.json()
        ]

    def load_version(self, baseline_id: str, version: str) -> dict:
        resp = self._get(f"/projects/{baseline_id}/versions/{version}")
        _raise_for_error(resp)
        data = resp.json()
        etag = resp.headers.get("ETag")
        if etag:
            self._etags[(baseline_id, version)] = etag
        self._write_cache(baseline_id, version, data)
        return data

    def active_version(self, baseline_id: str) -> Optional[str]:
        info = self.get_project(baseline_id)
        return info.active_version if info else None

    def set_active_version(self, baseline_id: str, version: str) -> None:
        resp = self._put(f"/projects/{baseline_id}/active", json={"version": version})
        _raise_for_error(resp)

    def active_baseline_path(self, baseline_id: str) -> Optional[Path]:
        try:
            version = self.active_version(baseline_id)
            if not version:
                return None
            self.load_version(baseline_id, version)  # refresh cache
            self._write_active_marker(baseline_id, version)
            return self._cache_path(baseline_id, version)
        except BaselineError:
            return self._offline_active_path(baseline_id)

    def _active_marker_path(self, baseline_id: str) -> Path:
        return self.cache_root / baseline_id / "active.json"

    def _write_active_marker(self, baseline_id: str, version: str) -> None:
        marker = self._active_marker_path(baseline_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"active": version}), encoding="utf-8")

    def _offline_active_path(self, baseline_id: str) -> Optional[Path]:
        # Prefer the last-known *active* version (which may be an older pinned
        # publish), not merely the newest cached one.
        marker = self._active_marker_path(baseline_id)
        if marker.exists():
            try:
                version = json.loads(marker.read_text(encoding="utf-8")).get("active")
            except (OSError, ValueError):
                version = None
            if version:
                path = self._cache_path(baseline_id, version)
                if path.exists():
                    return path
        # Fallback: numeric-newest by version (NOT lexical — .10 must beat .2).
        vdir = self.cache_root / baseline_id / "versions"
        versions = versioning.sort_versions([p.stem for p in vdir.glob("*.json")]) if vdir.exists() else []
        return self._cache_path(baseline_id, versions[-1]) if versions else None

    def version_path(self, baseline_id: str, version: str) -> Path:
        path = self._cache_path(baseline_id, version)
        # Drafts are mutable server-side; always revalidate so we never serve a
        # stale cached draft. Fall back to the cache only when offline.
        try:
            self.load_version(baseline_id, version)
        except BaselineError:
            if not path.exists():
                raise
        return path

    def new_draft(self, baseline_id: str, parent_version: str) -> dict:
        # Same append-only derivation as the filesystem repo, computed client-side.
        parent = self.load_version(baseline_id, parent_version)
        return versioning.new_draft_from(parent, today_str(), self.list_versions(baseline_id))

    def save_draft(self, baseline: dict) -> str:
        baseline_id = str(baseline.get("baseline_id", ""))
        version = str(baseline.get("version", ""))
        headers = {}
        etag = self._etags.get((baseline_id, version))
        if etag:
            headers["If-Match"] = etag
        resp = self._post(f"/projects/{baseline_id}/drafts", json=baseline, headers=headers)
        _raise_for_error(resp)
        body = resp.json()
        new_etag = body.get("etag")
        if new_etag:
            self._etags[(baseline_id, str(body["version"]))] = new_etag
        return str(body["version"])

    def publish(self, baseline_id: str, version: str) -> dict:
        resp = self._post(f"/projects/{baseline_id}/versions/{version}/publish")
        _raise_for_error(resp)
        return resp.json()

    # -- documents -----------------------------------------------------
    def add_document(self, baseline_id: str, src_path: Path) -> Path:
        src_path = Path(src_path)
        if not src_path.exists():
            raise BaselineError(f"文档不存在：{src_path}")
        with src_path.open("rb") as fh:
            resp = self._post(
                f"/projects/{baseline_id}/documents",
                files={"file": (src_path.name, fh)},
            )
        _raise_for_error(resp)
        return src_path  # GUI ignores the return; the original stays local.

    # -- mapping -------------------------------------------------------
    @staticmethod
    def _to_info(item: dict) -> ProjectInfo:
        return ProjectInfo(
            baseline_id=str(item["baseline_id"]),
            name=str(item.get("name", item["baseline_id"])),
            active_version=item.get("active_version"),
            versions=[str(v) for v in item.get("versions", [])],
        )
