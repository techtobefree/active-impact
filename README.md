# Active Impact

A service-project non-profit platform. Local **impact projects** meet volunteers;
a QR check-in signs the project's waiver and records time; volunteers earn
**impact tokens** (1 per hour) tracked in a Postgres ledger; a **catalog** matches
needs and offers priced in tokens; anyone can tip tokens to anyone.

Essentialist MVP: **FastAPI + Postgres + Caddy** and a **no-build vanilla-JS PWA**,
deployable to one VM with `docker compose`.

## Quickstart (local)

Everything in Docker:

```bash
docker compose up -d --build          # app + Postgres
open http://localhost:8000            # register an account and go
```

Fast dev loop (auto-reload), against a Postgres container:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
docker compose up -d postgres         # just the DB (published on 127.0.0.1:5433)
python -m app.db --init               # apply schema (idempotent)
uvicorn app.main:app --reload         # http://localhost:8000
python scripts/seed.py                # optional demo data (users ana/ben/mia, pw: password123)
```

## Tests

```bash
. .venv/bin/activate
docker compose up -d postgres
python -m pytest -q                   # 136 tests: auth, ledger invariants, all domains
python scripts/smoke.py http://localhost:8000   # end-to-end happy-path probe
```

## Deploy (given a domain)

The full runbook is [`docs/design/DEPLOYMENT.md`](docs/design/DEPLOYMENT.md). In short:

```bash
# On a VM with Docker, a DNS A record -> its IP, and ports 80/443 open:
./deploy.sh user@your-vm             # ships code, then guides .env creation
# set SITE_ADDRESS=your-domain and POSTGRES_PASSWORD in .env on the VM, then:
./deploy.sh user@your-vm             # builds, applies schema, starts Caddy+app+Postgres
curl https://your-domain/api/health  # {"ok":true,"db":true}  (Caddy auto-issues the cert)
```

Config is two knobs (`SITE_ADDRESS`, `POSTGRES_PASSWORD`) — see `.env.example`.
Backups: `scripts/backup.sh` (cron-able `pg_dump`).

## Layout

```
app/        FastAPI: db, auth, tokens (ledger), users, projects, checkin, catalog, images
db/         schema.sql (idempotent, applied on every boot)
public/     the PWA — index.html shell, app.js router, api.js, ui.js, views/*
tests/      pytest (real Postgres test DB)
scripts/    smoke.py, seed.py, backup.sh
docs/       intent + the design tree (start at docs/design/OVERVIEW.md)
```

## Documentation

| Doc | What it is |
|-----|------------|
| [`docs/intent.md`](docs/intent.md) | The founder's verbatim intent |
| [`docs/design/OVERVIEW.md`](docs/design/OVERVIEW.md) | Architecture, binding decisions, constants — **the entry point** |
| [`docs/design/DOMAIN.md`](docs/design/DOMAIN.md) | Schema, token accounting, invariants |
| [`docs/design/API.md`](docs/design/API.md) | The HTTP contract |
| [`docs/design/FRONTEND.md`](docs/design/FRONTEND.md) | PWA screens + flows |
| [`docs/design/DEPLOYMENT.md`](docs/design/DEPLOYMENT.md) | The go-live runbook |
| [`docs/design/BUILD_PLAN.md`](docs/design/BUILD_PLAN.md) | TDD milestones + definition of done |

Impact tokens are internal points with no monetary value — a way to recognize
volunteered time and social capital. See the intent doc for the philosophy.
