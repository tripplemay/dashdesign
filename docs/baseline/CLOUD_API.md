# Baseline Cloud API Contract (Phase B)

> **Status: implemented.** Server: `cloud/server/` (FastAPI + SQLAlchemy JSONB +
> token/role auth + ETag optimistic concurrency + server-side governance/publish
> gate + merge jobs). Desktop client: `cloud/client.py`
> (`HttpBaselineRepository`, `requests`-only, local read-cache). GUI wiring:
> `ui/baseline_service.py` selects the repository by config; the cloud endpoint +
> token are set in **文件 → 设置 → 云端基线** (`ui/api_config.py`). Deploy notes:
> [DEPLOY.md](./DEPLOY.md). Tests: `tests/test_cloud_{store,api,client}.py`.

Phase A ships the baseline system fully local: `baseline/` (Qt-free domain) +
`ui/baseline_service.py` (GUI bridge). The desktop client depends only on the
`BaselineRepository` surface. Phase B replaces the filesystem repository with a
thin HTTP client against the REST contract below — the GUI does not change.

**Chosen backend (Phase B):** self-built FastAPI (REST) + managed Postgres
(JSONB baseline + append-only version table) + object storage (OSS) for
uploaded documents, deployed on a domestic Serverless platform (Aliyun FC /
Tencent SCF, scale-to-zero). Rationale: best fit for the highly-customized
domain model (evidenced_text / evidence_index / governance), domestic network
+ PIPL compliance, and near-BaaS ops for a small number of seats.

## Repository surface to preserve

`BaselineRepository` (see `baseline/store.py`): `list_projects`,
`get_project`, `create_project`, `list_versions`, `load_version`,
`active_version`/`set_active_version`, `active_baseline_path`, `new_draft`,
`save_draft`, `publish`, `add_document`.

## REST endpoints (proposed)

```
GET    /projects                          -> [ProjectInfo]
POST   /projects                          {baseline}            -> ProjectInfo
GET    /projects/{id}                     -> ProjectInfo
GET    /projects/{id}/versions            -> [version, status]
GET    /projects/{id}/versions/{version}  -> baseline
PUT    /projects/{id}/active              {version}
POST   /projects/{id}/drafts              {baseline}            -> {version}     # 201
POST   /projects/{id}/versions/{version}/publish                -> baseline      # governance-gated
POST   /projects/{id}/documents           multipart(file)       -> {document_id, url}
POST   /projects/{id}/merge-jobs          {document_id}         -> {job_id}      # server-side extract+merge
GET    /merge-jobs/{job_id}               -> {status, report}
```

## Cross-cutting

- **Auth / sharing:** project membership + roles (editor / reviewer / admin),
  org multitenancy. Credentials reuse the QSettings/api_config pattern (add a
  cloud endpoint + token alongside the image-API key).
- **Concurrency:** optimistic lock via `parent_version` / ETag `If-Match`;
  server returns `409` on mismatch, client rebases and re-submits. Publish is a
  server-authoritative state transition (draft -> published, immutable).
- **Governance authoritative on the server:** schema validation, blocked-keyword
  and claims_policy screening, and the publish gate run server-side so a patched
  client cannot bypass them. LLM merge/validate/publish move to the server;
  document parsing may stay client-side (originals need not leave the machine —
  only extracted text is sent).
- **Offline:** the client keeps the local repository as a read cache and falls
  back to the bundled baseline when the cloud is unreachable.
