"""FastAPI application implementing the Phase B baseline cloud contract.

Endpoints follow ``docs/baseline/CLOUD_API.md``. Governance, schema validation
and the publish gate run here (server-authoritative); optimistic concurrency is
enforced via ETag / If-Match. The app is created via ``create_app`` so settings,
the chat factory and (in tests) an in-memory SQLite engine can be injected.
"""

from __future__ import annotations

import hmac
from typing import Callable, Iterator, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from baseline.errors import BaselineError, GovernanceError, ValidationError
from baseline.llm import make_chat

from cloud.server import auth, db, mergejobs, schemas
from cloud.server.config import Settings, load_settings
from cloud.server.docstore import build_document_store
from cloud.server.store import ConflictError, NotFoundError, SqlBaselineStore

# --- domain-exception -> HTTP mapping -----------------------------------
_ERROR_MAP = [
    (NotFoundError, 404, "not_found"),
    (ConflictError, 409, "conflict"),
    (ValidationError, 422, "validation_error"),
    (GovernanceError, 422, "governance_error"),
    (BaselineError, 400, "baseline_error"),
]


def _error_response(exc: BaselineError) -> JSONResponse:
    # Auth/permission errors carry an explicit status + code (_HTTPError).
    explicit_status = getattr(exc, "status", None)
    explicit_code = getattr(exc, "code", None)
    if explicit_status and explicit_code:
        messages = getattr(exc, "messages", None) or [str(exc)]
        return JSONResponse(
            status_code=explicit_status, content={"code": explicit_code, "messages": messages}
        )
    for cls, status, code in _ERROR_MAP:
        if isinstance(exc, cls):
            messages = getattr(exc, "messages", None) or [str(exc)]
            return JSONResponse(
                status_code=status, content={"code": code, "messages": messages}
            )
    return JSONResponse(status_code=400, content={"code": "baseline_error", "messages": [str(exc)]})


def _default_chat_factory(model: Optional[str]) -> Callable:
    return make_chat("", "", model or "")


def create_app(
    settings: Optional[Settings] = None,
    engine=None,
    chat_factory: Optional[Callable[[Optional[str]], Callable]] = None,
) -> FastAPI:
    settings = settings or load_settings()
    engine = engine or db.make_engine(settings.db_url)
    db.create_all(engine)
    session_factory = db.make_session_factory(engine)
    doc_store = build_document_store(settings)
    chat_factory = chat_factory or _default_chat_factory

    app = FastAPI(title="DashDesign Baseline Cloud", version="1.0")
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.doc_store = doc_store
    app.state.chat_factory = chat_factory

    # Bootstrap a global-admin principal from the configured admin token.
    if settings.admin_token:
        with session_factory() as boot:
            auth.ensure_user_with_token(boot, "admin", "Bootstrap Admin", settings.admin_token, True)
            boot.commit()

    @app.exception_handler(BaselineError)
    async def _handle_baseline_error(_request: Request, exc: BaselineError):
        return _error_response(exc)

    @app.exception_handler(IntegrityError)
    async def _handle_integrity_error(_request: Request, _exc: IntegrityError):
        # A check-then-insert race (two concurrent creates of the same
        # project/version) trips a PK/unique constraint at flush; surface it as
        # the contract's 409 conflict rather than a generic 500. The session is
        # already rolled back by get_session's except-branch.
        return JSONResponse(
            status_code=409, content={"code": "conflict", "messages": ["资源冲突（并发创建），请刷新后重试"]}
        )

    # --- dependencies ---------------------------------------------------
    def get_session(request: Request) -> Iterator[Session]:
        session = request.app.state.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_principal(
        session: Session = Depends(get_session),
        authorization: str = Header(default=""),
    ) -> auth.Principal:
        token = ""
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        principal = auth.principal_for_token(session, token)
        if principal is None:
            raise _HTTPError(401, "unauthorized", ["缺少或无效的访问令牌"])
        return principal

    def get_store(session: Session = Depends(get_session)) -> SqlBaselineStore:
        return SqlBaselineStore(session, app.state.doc_store)

    def require_role(
        baseline_id: str,
        principal: auth.Principal,
        session: Session,
        minimum: str,
    ) -> None:
        role = auth.role_for(session, baseline_id, principal)
        if not auth.role_at_least(role, minimum):
            raise _HTTPError(403, "forbidden", [f"需要 {minimum} 及以上权限"])

    # --- projects -------------------------------------------------------
    @app.get("/projects", response_model=List[schemas.ProjectInfoOut])
    def list_projects(
        store: SqlBaselineStore = Depends(get_store),
        principal: auth.Principal = Depends(get_principal),
    ):
        out = []
        for project in store.list_projects():
            if not principal.is_admin and auth.role_for(store.s, project.baseline_id, principal) is None:
                continue
            out.append(_project_info(store, project.baseline_id))
        return out

    @app.post("/projects", response_model=schemas.ProjectInfoOut, status_code=201)
    def create_project(
        baseline: dict,
        store: SqlBaselineStore = Depends(get_store),
        principal: auth.Principal = Depends(get_principal),
    ):
        store.create_project(baseline, owner_user_id=principal.user_id)
        return _project_info(store, str(baseline["baseline_id"]))

    @app.get("/projects/{baseline_id}", response_model=schemas.ProjectInfoOut)
    def get_project(
        baseline_id: str,
        store: SqlBaselineStore = Depends(get_store),
        principal: auth.Principal = Depends(get_principal),
    ):
        require_role(baseline_id, principal, store.s, "reviewer")
        if store.get_project(baseline_id) is None:
            raise NotFoundError(f"项目不存在：{baseline_id}")
        return _project_info(store, baseline_id)

    # --- versions -------------------------------------------------------
    @app.get("/projects/{baseline_id}/versions", response_model=List[schemas.VersionSummaryOut])
    def list_versions(
        baseline_id: str,
        store: SqlBaselineStore = Depends(get_store),
        principal: auth.Principal = Depends(get_principal),
    ):
        require_role(baseline_id, principal, store.s, "reviewer")
        summaries = []
        for version in store.list_versions(baseline_id):
            data, _ = store.load_version(baseline_id, version)
            summaries.append({"version": version, "status": data.get("status", "draft")})
        return summaries

    @app.get("/projects/{baseline_id}/versions/{version}")
    def load_version(
        baseline_id: str,
        version: str,
        response: Response,
        store: SqlBaselineStore = Depends(get_store),
        principal: auth.Principal = Depends(get_principal),
    ):
        require_role(baseline_id, principal, store.s, "reviewer")
        data, etag = store.load_version(baseline_id, version)
        response.headers["ETag"] = etag
        return data

    @app.put("/projects/{baseline_id}/active", status_code=204)
    def set_active(
        baseline_id: str,
        body: schemas.SetActiveIn,
        store: SqlBaselineStore = Depends(get_store),
        principal: auth.Principal = Depends(get_principal),
    ):
        require_role(baseline_id, principal, store.s, "editor")
        store.set_active_version(baseline_id, body.version)
        return Response(status_code=204)

    @app.post("/projects/{baseline_id}/drafts", response_model=schemas.CreateDraftOut, status_code=201)
    def save_draft(
        baseline_id: str,
        baseline: dict,
        store: SqlBaselineStore = Depends(get_store),
        principal: auth.Principal = Depends(get_principal),
        if_match: str = Header(default=""),
    ):
        require_role(baseline_id, principal, store.s, "editor")
        if str(baseline.get("baseline_id", "")) != baseline_id:
            raise BaselineError("请求体 baseline_id 与路径不一致")
        version, etag = store.save_draft(baseline, if_match=if_match or None)
        return {"version": version, "etag": etag}

    @app.post("/projects/{baseline_id}/versions/{version}/publish")
    def publish(
        baseline_id: str,
        version: str,
        store: SqlBaselineStore = Depends(get_store),
        principal: auth.Principal = Depends(get_principal),
    ):
        require_role(baseline_id, principal, store.s, "editor")
        return store.publish(baseline_id, version)

    # --- documents ------------------------------------------------------
    @app.post("/projects/{baseline_id}/documents", response_model=schemas.DocumentOut)
    async def upload_document(
        baseline_id: str,
        file: UploadFile,
        store: SqlBaselineStore = Depends(get_store),
        principal: auth.Principal = Depends(get_principal),
    ):
        require_role(baseline_id, principal, store.s, "editor")
        data = await file.read()
        doc = store.add_document(baseline_id, file.filename or "document", data)
        return {"document_id": doc.id, "url": doc.storage_url}

    # --- merge jobs -----------------------------------------------------
    @app.post("/projects/{baseline_id}/merge-jobs", response_model=schemas.MergeJobCreateOut)
    def create_merge_job(
        baseline_id: str,
        body: schemas.MergeJobIn,
        background: BackgroundTasks,
        store: SqlBaselineStore = Depends(get_store),
        principal: auth.Principal = Depends(get_principal),
    ):
        require_role(baseline_id, principal, store.s, "editor")
        job = store.create_merge_job(baseline_id, body.document_id)
        job_id = job.id
        # The queued row is committed by get_session when this route returns; the
        # LLM extraction runs afterwards in its own short-lived session so it
        # never holds this request's pooled DB connection open across the network
        # call. Each status transition commits separately -> observable by polling.
        background.add_task(
            _run_merge_job,
            app.state.session_factory,
            app.state.doc_store,
            app.state.chat_factory,
            job_id,
            body,
        )
        return {"job_id": job_id}

    @app.get("/merge-jobs/{job_id}", response_model=schemas.MergeJobStatusOut)
    def get_merge_job(
        job_id: str,
        store: SqlBaselineStore = Depends(get_store),
        principal: auth.Principal = Depends(get_principal),
    ):
        job = store.get_merge_job(job_id)
        require_role(job.baseline_id, principal, store.s, "reviewer")
        return {"status": job.status, "report": job.report, "error": job.error}

    # --- shared app config (client bootstrap) ---------------------------
    def _check_admin_password(provided: str) -> None:
        expected = app.state.settings.admin_password
        if not expected or not hmac.compare_digest(provided or "", expected):
            raise _HTTPError(403, "forbidden", ["管理密码错误，或服务端未配置管理密码"])

    @app.get("/app-config", response_model=schemas.AppConfigModel)
    def get_app_config(
        store: SqlBaselineStore = Depends(get_store),
        principal: auth.Principal = Depends(get_principal),
    ):
        # Any authenticated client (incl. the shared client token) may read it.
        return store.get_app_config()

    @app.put("/app-config", response_model=schemas.AppConfigModel)
    def put_app_config(
        body: schemas.AppConfigModel,
        store: SqlBaselineStore = Depends(get_store),
        x_admin_password: str = Header(default=""),
    ):
        # Gated by the admin password only — the admin needs no bearer token.
        _check_admin_password(x_admin_password)
        return store.set_app_config(body.model_dump(), updated_by="admin")

    @app.post("/admin/verify", response_model=schemas.AdminVerifyOut)
    def admin_verify(x_admin_password: str = Header(default="")):
        _check_admin_password(x_admin_password)
        return {"ok": True}

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    # --- internal helpers ----------------------------------------------
    def _project_info(store: SqlBaselineStore, baseline_id: str) -> dict:
        project = store.get_project(baseline_id)
        return {
            "baseline_id": baseline_id,
            "name": project.name if project else baseline_id,
            "active_version": project.active_version if project else None,
            "versions": store.list_versions(baseline_id),
        }

    return app


def _run_merge_job(
    session_factory,
    doc_store,
    chat_factory: Callable[[Optional[str]], Callable],
    job_id: str,
    body: schemas.MergeJobIn,
) -> None:
    """Background worker: extract+merge in a fresh session. Never raises.

    Runs OUTSIDE the request transaction so the LLM network call does not hold a
    pooled DB connection open. Commits ``running`` then the final ``done``/``error``
    separately so a concurrent GET /merge-jobs poller can observe progress.
    """
    session = session_factory()
    try:
        store = SqlBaselineStore(session, doc_store)
        job = session.get(db.MergeJob, job_id)
        if job is None:
            return
        job.status = "running"
        session.commit()
        try:
            active = store.active_version(job.baseline_id)
            if not active:
                raise BaselineError("项目暂无活跃版本，无法合并")
            current, _ = store.load_version(job.baseline_id, active)
            if body.text:
                parsed = mergejobs.parsed_from_text(body.text, body.filename or "uploaded.txt")
            elif body.document_id:
                doc = session.get(db.Document, body.document_id)
                # Re-check tenant isolation at parse time (defense in depth).
                if doc is None or doc.baseline_id != job.baseline_id:
                    raise NotFoundError(f"文档不存在：{body.document_id}")
                parsed = mergejobs.parsed_from_storage_url(doc.storage_url, doc.filename)
            else:
                raise BaselineError("需要提供 document_id 或 text 之一")
            chat = chat_factory(body.model)
            job.report = mergejobs.run_extraction(parsed, current, chat)
            job.status = "done"
        except Exception as exc:  # noqa: BLE001 - surfaced via job.error
            job.status = "error"
            job.error = str(exc)[:1000]
        session.commit()
    finally:
        session.close()


class _HTTPError(BaselineError):
    """Auth/permission error carrying an explicit HTTP status."""

    def __init__(self, status: int, code: str, messages: List[str]) -> None:
        self.status = status
        self.code = code
        self.messages = messages
        super().__init__("；".join(messages))
