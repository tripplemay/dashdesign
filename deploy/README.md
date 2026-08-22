# Deploy the baseline cloud (any Linux VPS, always-on + Docker)

An always-on single box running the FastAPI server + Postgres via Docker Compose.
Simplest reliable shape for a small team: no serverless cold starts, merge jobs
run normally, setup is just SSH.

**This is cloud-agnostic** — any Linux VPS with a public IP works (your own box,
Hetzner, DigitalOcean, Vultr, Aliyun ECS, …). Nothing here is Aliyun-specific
except the *optional* OSS document store (§9), which is off by default. If you
already have a VPS, **skip step 1** and start at step 2.

Documents default to a local volume; Postgres runs as a container. Upgrade paths
(managed Postgres, OSS object storage) are in §9. Sizing: **2 vCPU / 2 GB / 40 GB
disk** is plenty for a small team.

---

## 0. What you provide vs. what's automated

You run the box-level steps (a VPS with a public IP, open ports, SSH). Everything
else is in this folder: `Dockerfile`, `docker-compose.yml`, `.env.example`,
`Caddyfile`.

---

## 1. Provision a box (skip if you already have a VPS)

Any Ubuntu 22.04 / Debian 12 / Alibaba Cloud Linux VPS with a public IP and
~2 vCPU / 2 GB / 40 GB disk. On Aliyun that's an ECS instance; on Hetzner /
DigitalOcean / Vultr it's their smallest-but-one droplet/CX plan.

**Open these ports** in the VPS firewall / security group (and in `ufw` if used):
   - `22` (SSH) — restrict to your IP if possible.
   - For production TLS: `80` and `443` (open to `0.0.0.0/0` so Let's Encrypt
     can validate). The API port stays bound to `127.0.0.1` and is never public.

> **Network note.** Clients reach the server over HTTP(S), so the box just needs
> to be reachable from wherever your desktop users are. If your team is in China
> and the VPS is overseas, expect higher latency and possible interference — a
> domestic box or one with a China-friendly route is smoother. Data-residency
> (PIPL) is your call.

## 2. Install Docker on the box (SSH)

> Run these yourself. In this chat you can prefix a command with `! ` to run it
> in the session, e.g. `! ssh root@<ECS_IP>`.

```bash
ssh root@<ECS_PUBLIC_IP>

# Docker Engine + compose plugin
curl -fsSL https://get.docker.com | sh          # or: apt-get install -y docker.io docker-compose-plugin
systemctl enable --now docker
docker version && docker compose version
```

## 3. Get the code onto the box

```bash
# Option A: clone (repo is public)
git clone https://github.com/tripplemay/dashdesign.git
cd dashdesign/deploy

# Option B: from your laptop, if the repo is private
#   scp -r /Users/yixingzhou/project/dashdesign root@<ECS_IP>:/root/dashdesign
```

## 4. Configure secrets

```bash
cp .env.example .env
# generate strong values:
openssl rand -base64 24   # -> DB_PASSWORD
openssl rand -base64 24   # -> BASELINE_ADMIN_TOKEN
nano .env                 # paste them in; leave BASELINE_DOC_STORE=local for now
```

## 5. Start it

```bash
# Quick HTTP test from the VPS itself (the API is loopback-only):
docker compose up -d --build
docker compose ps
curl -s http://localhost:8000/healthz          # -> {"status":"ok"}
```

If port `8000` is already in use, set `BASELINE_HOST_PORT=8030` in `.env` and
test `curl -s http://localhost:8030/healthz` instead.

## 6. Create a login token for each teammate

```bash
# Run the admin CLI inside the api container. --admin makes a global admin;
# omit it and grant per-project roles instead.
docker compose exec api python -m cloud.server.manage create-token alice --name "Alice"
# -> prints:  token=XXXX…  (give this to Alice; it is shown only once)

# Per-project role (reviewer | editor | admin) once a project exists:
docker compose exec api python -m cloud.server.manage add-member <baseline_id> alice editor
docker compose exec api python -m cloud.server.manage list-projects
```

The bootstrap `BASELINE_ADMIN_TOKEN` from `.env` is itself a global-admin token
you can use directly from the desktop app.

## 7. Point the desktop app at the server

In DashDesign: **文件 → 设置 → 云端基线**
- 服务地址: `https://<your-domain>`.
- 访问令牌: the token from step 6.

Save → the app immediately switches to the cloud repository. Existing **local**
projects are not auto-uploaded; re-create/import them once so they live in the
cloud and every seat sees them.

---

## 8. Production TLS (strongly recommended)

Bearer tokens must not travel over plain HTTP. Add a domain + Caddy auto-HTTPS:

1. Point an A-record (e.g. `baseline.example.com`) at the VPS public IP.
2. Open ports `80` and `443` in the security group. Keep the API bound to loopback.
3. If this is a dedicated host, set `SITE_DOMAIN=baseline.example.com` in `.env`
   and let the bundled Caddy container terminate TLS:
   ```bash
   docker compose --profile tls up -d --build
   ```
4. If the host already runs nginx, leave Caddy disabled and install an nginx
   vhost that proxies to `127.0.0.1:${BASELINE_HOST_PORT}`. The production
   `dash.vpanel.cc` example is in `deploy/dash.vpanel.cc.nginx.conf`.

Caddy fetches a Let's Encrypt certificate automatically. With host nginx, issue
and renew the certificate using the host's existing Certbot setup. In either
case, use `https://<domain>` in the app.

## 9. Upgrades (optional)

- **Managed Postgres (RDS/PolarDB):** create an instance, then set
  `BASELINE_DB_URL=postgresql+psycopg://user:pass@<rds-host>:5432/baseline` in
  `.env` and remove the `db` service from compose. Gives you managed backups.
- **OSS for documents:** create a bucket in the same region, an OSS-scoped RAM
  user + AccessKey, then in `.env` set `BASELINE_DOC_STORE=oss`,
  `BASELINE_OSS_BUCKET`, `BASELINE_OSS_ENDPOINT` (internal endpoint), and the
  `ALIBABA_CLOUD_ACCESS_KEY_*` pair.
- **Backups:** `docker compose exec db pg_dump -U baseline baseline > backup.sql`
  on a cron; snapshot the ECS disk.

## Operate

```bash
docker compose logs -f api      # tail server logs
docker compose pull && docker compose up -d --build   # update after git pull
docker compose down             # stop (volumes persist)
```
