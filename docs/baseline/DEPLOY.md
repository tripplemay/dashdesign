# Baseline Cloud Backend — Deploy (Phase B)

The backend is a FastAPI app (`cloud/server/`) that persists baselines in an
append-only, JSONB-backed store and enforces schema + governance + the publish
gate server-side. It runs on SQLite (dev/tests) and managed Postgres (prod) from
one codebase, and deploys either as a long-running service or scale-to-zero
serverless function. The desktop client (`cloud/client.py`) talks to it over HTTP
with a bearer token; it never imports any server package.

## 1. Install

```bash
python -m pip install -r requirements.txt -r requirements-cloud.txt
```

## 2. Configuration (environment variables)

| Var | Default | Purpose |
|-----|---------|---------|
| `BASELINE_DB_URL` | `sqlite:///./baseline_cloud.db` | SQLAlchemy DSN. Prod: `postgresql+psycopg://user:pass@host/db` |
| `BASELINE_DOC_STORE` | `local` | `local` (filesystem) or `oss` (object storage) |
| `BASELINE_DOC_ROOT` | `./baseline_documents` | Local document-store root |
| `BASELINE_ADMIN_TOKEN` | — | If set, bootstraps a global-admin bearer token on startup |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | — | Gateway used by server-side merge jobs |
| `DASHDESIGN_BASELINE_MODEL` | `gpt-4o` | Default text model for extraction |

## 3. Run locally

```bash
export BASELINE_ADMIN_TOKEN="dev-admin-token"
uvicorn cloud.server.asgi:app --host 0.0.0.0 --port 8000
curl -s localhost:8000/healthz
```

## 4. Onboard users

`BASELINE_ADMIN_TOKEN` gives you a global admin. Mint per-user tokens and grant
project roles with the admin CLI (roles: `reviewer` < `editor` < `admin`):

```bash
python -m cloud.server.manage create-token alice --name "Alice"
python -m cloud.server.manage add-member <baseline_id> alice editor
```

Tokens are printed once and stored only as SHA-256 hashes.

## 5. Postgres (prod)

Point `BASELINE_DB_URL` at managed Postgres; `Base.metadata.create_all` provisions
the tables on first boot (swap in Alembic migrations if you need controlled schema
evolution). Baseline documents are stored in a `JSONB` column, so they are
queryable/indexable server-side.

## 6. Serverless (Aliyun FC / Tencent SCF)

`cloud/server/asgi.py` exposes `handler = Mangum(app)` for an API-Gateway → ASGI
bridge. Package the repo + `requirements-cloud.txt` into the function image, set
the handler to `cloud.server.asgi.handler`, and configure the env vars above.
Use a managed Postgres DSN (not SQLite) so state survives scale-to-zero, and set
`BASELINE_DOC_STORE=oss` with an injected bucket client for uploaded documents.

> **Merge jobs on serverless.** Merge extraction runs in a FastAPI background
> task (its own short-lived DB session, so it never pins the request's pooled
> connection across the LLM call). Background-after-response is reliable on a
> long-running service (uvicorn/Docker) but **not** on scale-to-zero functions,
> which may freeze once the response is sent. For serverless, either run the
> merge endpoints on a small always-on instance, or replace the background task
> with a queue-backed worker (the job row + status columns are already designed
> for that). The persistence spine (projects/versions/drafts/publish/documents)
> is fully serverless-safe.

## 7. Docker (long-running service)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-cloud.txt
COPY . .
CMD ["uvicorn", "cloud.server.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 8. Point the desktop client at the cloud

In the app: **文件 → 设置 → 云端基线**, fill in the service URL and the user's
bearer token. Both set → the app uses the cloud repository (shared, multi-user,
server-governed); either empty → it uses the local baseline library. The client
keeps a local JSON cache under AppData so `--baseline <path>` and offline reads
keep working.
