# Deployment

> Copies the reference app's proven, deliberately minimal topology (D17): three
> containers, two compose files, a 5-line Caddyfile, tarball-over-SSH deploys,
> and a small `.env`. Plus app healthcheck, log rotation, backup script.

## The deploy command — `./deploy.sh` ("update the web")

One command, run from the repo root, deploys the current tree to the server:

```bash
./deploy.sh          # ships code + .env, rebuilds the prod stack, health + smoke
```

It reads **`.env`** (gitignored — never committed):

```bash
SITE_ADDRESS=:80                       # ":80" = HTTP on the server IP;
                                       # "shadow.my, www.shadow.my" = auto-HTTPS
POSTGRES_PASSWORD=<set once, keep stable>
DEPLOY_HOST=root@192.241.130.180       # the droplet
```

**Two-address model.** `SITE_ADDRESS=:80` serves plain HTTP on the droplet's IP —
used while a domain's DNS isn't pointing at the box yet. Once the domain's A
records resolve to the droplet, set `SITE_ADDRESS=shadow.my, www.shadow.my` and
re-run `./deploy.sh`; Caddy then obtains and auto-renews Let's Encrypt certs.
(Prereqs for the domain: DNS A records → droplet IP, ports 80/443 open. Verify
with `curl https://cloudflare-dns.com/dns-query?name=<domain>&type=A -H accept:application/dns-json`.)

**Current live staging:** `http://192.241.130.180` (SITE_ADDRESS=:80). The droplet
was prepped with a 2 GB swapfile (it's a 512 MB box) + Docker via get.docker.com.

**Workflow:** "update local" (redeploy the local `:3032` uvicorn) happens on every
change; "update the web" runs `./deploy.sh`. Both are idempotent.

---

## Files (reference)

## Files

### `Caddyfile` (complete)

```
# Caddy reverse proxy with automatic HTTPS.
# SITE_ADDRESS is your domain (e.g. impact.example.org) — Caddy fetches and
# auto-renews a Let's Encrypt cert for it. Use ":80" for local/no-TLS testing.
{$SITE_ADDRESS} {
	encode zstd gzip
	reverse_proxy app:8000
}
```

### `Dockerfile` (complete)

```dockerfile
# Active Impact app image. Self-contained: runs DB migrations then serves.
FROM python:3.12-slim
WORKDIR /srv
# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
# `app.db --init` runs `alembic upgrade head` — idempotent, safe on every boot.
# --proxy-headers trusts Caddy's X-Forwarded-Proto so request.url.scheme is https
# in prod (safe: the app has no published ports — only Caddy can reach it).
CMD ["sh", "-c", "python -m app.db --init && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
```

### `docker-compose.yml` — dev (spec)

Same shape as prod minus Caddy; for local hacking and pytest.

- `postgres`: `postgres:16-alpine`; env `POSTGRES_USER/PASSWORD/DB =
  postgres/postgres/impact`; volume `pgdata`; healthcheck `pg_isready -U postgres
  -d impact` (3s/3s/20); **publishes `127.0.0.1:5433:5432`** (for pytest/psql —
  loopback only, one delta from the reference).
- `app`: `build: .`; `DATABASE_URL=postgres://postgres:postgres@postgres:5432/impact`;
  publishes `8000:8000`; `depends_on: postgres: condition: service_healthy`.
- All services `restart: unless-stopped` + the logging block below.
- Header comment: `docker compose up -d --build` → `http://localhost:8000`.

### `docker-compose.prod.yml` (spec)

Mirror the reference verbatim with these substitutions — service topology
`caddy → app → postgres`:

- `caddy`: `caddy:2-alpine`; ports `80:80`, `443:443`; env `SITE_ADDRESS:
  ${SITE_ADDRESS:-:80}`; volumes `./Caddyfile:/etc/caddy/Caddyfile:ro`,
  `caddy_data:/data`, `caddy_config:/config`; `depends_on: app`.
- `app`: `build: .`; env `DATABASE_URL:
  postgres://postgres:${POSTGRES_PASSWORD}@postgres:5432/impact`; **no published
  ports** (comment: only reachable via caddy); `depends_on: postgres:
  condition: service_healthy`; **healthcheck** (addition):
  ```yaml
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://localhost:8000/api/health', timeout=3)"]
    interval: 30s
    timeout: 5s
    retries: 3
  ```
- `postgres`: as dev but `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}`, **no
  published ports**, same healthcheck.
- Every service gets `restart: unless-stopped` **and** log rotation (addition):
  ```yaml
  logging: { driver: json-file, options: { max-size: "10m", max-file: "3" } }
  ```
- Volumes: `pgdata`, `caddy_data`, `caddy_config`.
- Header comment: `cp .env.example .env` → edit → `docker compose -f
  docker-compose.prod.yml up -d --build`.

### `.env.example` (complete — the entire config surface, D16)

```bash
# Copy to .env for the production stack (docker-compose.prod.yml).

# Your domain. Caddy fetches an automatic HTTPS cert for it.
# Must have a DNS A record pointing to this server's IP BEFORE first boot.
# Use ":80" instead of a domain for local/no-TLS testing.
SITE_ADDRESS=impact.example.org

# Postgres password (internal to the compose network). Generate:
#   openssl rand -hex 24
POSTGRES_PASSWORD=change-me
```

No SECRET_KEY exists — session tokens are opaque DB rows; nothing is signed.

### `deploy.sh` (spec)

Adapt the reference script verbatim: usage `./deploy.sh user@host
[/remote/dir]`, default remote dir **`/opt/active-impact`**; tar excludes
`./.git ./.venv ./__pycache__ ./*.tgz ./node_modules`; scp → extract → **hard
fail with instructions if no `.env` on server** → `docker compose -f
docker-compose.prod.yml up -d --build`; final line prints the `… ps` check hint.
Note in header (reference gotcha): extraction overwrites in place and never
prunes — renamed/deleted files linger on the server.

### `scripts/backup.sh` (addition)

```bash
#!/bin/bash
# Dump the production database. Run on the VM (cron-able). Keeps 14 days.
set -e
cd "$(dirname "$0")/.."
mkdir -p backups
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U postgres impact > "backups/impact-$(date +%F).sql"
find backups -name 'impact-*.sql' -mtime +14 -delete
echo "backup written: backups/impact-$(date +%F).sql"
```

DEPLOY docs mention the cron one-liner: `0 3 * * * /opt/active-impact/scripts/backup.sh`.

## Runbook — given a URL, go live

Prerequisites (exactly three, as in the reference):

1. A VM (Ubuntu 22.04+) you can SSH into; Docker: `curl -fsSL https://get.docker.com | sh`.
   SSH as root, **or** give your user docker rights: `usermod -aG docker <user>`
   (deploy.sh runs `docker compose` over SSH — without this, step 2 fails on docker.sock)
2. **DNS A record** `<domain> → <VM IP>`, resolving **before** first boot
   (Caddy needs it to obtain the cert)
3. Ports open: `ufw allow 80 && ufw allow 443` (and 22)

Steps:

```bash
# 0. sanity: dig +short <domain>  →  must print the VM IP
./deploy.sh user@<vm>                      # 1. ship code (fails at .env guard — expected first time)
ssh user@<vm> 'cd /opt/active-impact && cp .env.example .env && nano .env'
#    SITE_ADDRESS=<domain>   POSTGRES_PASSWORD=$(openssl rand -hex 24)
./deploy.sh user@<vm>                      # 2. re-run → builds, applies schema, starts stack
curl -s https://<domain>/api/health        # 3. → {"ok":true,"db":true}   (cert may take ~30s)
python3 scripts/smoke.py https://<domain>  # 4. full happy-path probe (see below)
# 5. open https://<domain> on a phone → register → install prompt → done
```

Updating = edit code → re-run `./deploy.sh user@<vm>` (schema re-applies
idempotently; pgdata/certs persist in volumes). Rollback = redeploy a previous
checkout. Logs: `ssh … 'cd /opt/active-impact && docker compose -f
docker-compose.prod.yml logs -f app'`.

## `scripts/smoke.py` (spec)

Stdlib-only (`urllib`, `json`, `time`) so it runs anywhere Python 3 exists; takes
`BASE_URL`. Creates throwaway `smoke-<epoch>-a/b` users and walks the real flows,
asserting each step:

1. `GET /api/health` → ok+db
2. register a & b; login a
3. a creates a project (starts now, 120 expected minutes) → reads detail as
   creator: `am_leader` true, waiver v1 present, **`checkin_code` in the payload**
   (leader-only field — this is where smoke gets the code); fetches `/qr.svg`
   (200, `image/svg+xml`)
4. b resolves `GET /checkin/{code}` with that code → agrees (201) → duplicate
   agree → **409 `already_checked_in`**
5. a reads the roster (rows carry the participation `id`) → checks b out via
   `POST /participations/{id}/checkout` → `tokens_awarded == 0` (real clock,
   minutes≈0 — verifies flow; minting *math* is pytest's job with backdated rows)
6. b tips a 1 token → **409 `insufficient_balance`** (proves the ledger guard is
   live in prod)
7. a posts a free offer (price 0) → b claims → a accepts → claim `accepted`
8. unauthenticated `GET /api/me` → 401
9. print `SMOKE PASS`

(Throwaway users remain — harmless on a fresh instance, and the script is also
the acceptance gate in BUILD_PLAN M6.)

## Local development

- **Everything in Docker**: `docker compose up -d --build` → `http://localhost:8000`.
- **Fast loop**: `docker compose up -d postgres` → `python -m app.db --init` →
  `uvicorn app.main:app --reload` (static is served by FastAPI, so the PWA
  hot-loops on refresh). No env needed: `app/db.py` defaults `DATABASE_URL` to
  `postgres://postgres:postgres@localhost:5433/impact` — the dev-compose socket
  (DOMAIN.md).
- **Tests**: postgres up → `pytest` (conftest creates/truncates `impact_test` DB
  on the same 5433 socket — see BUILD_PLAN).
- Optional demo data for manual UX runs: `python scripts/seed.py` (dev-only;
  refuses unless DATABASE_URL points at localhost; inserts users/projects with
  **backdated participations** so wallets have tokens to play with).

## Consciously accepted (documented, not bugs — D19 etc.)

App connects as the postgres superuser · no CI · no monitoring beyond
healthchecks + `docker ps` · single uvicorn worker · sessions pruned only on
login · backups are a cron script, not managed · certs/data live in named
volumes on one VM.
