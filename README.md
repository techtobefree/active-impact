# Active Impact

A service-project non-profit platform: local **impact projects** meet volunteers;
QR check-in signs the waiver and records time; **impact tokens** (1/hour) live in
a Postgres ledger; a **catalog** matches needs and offers priced in tokens.

## Start here

| Doc | What it is |
|-----|------------|
| [`docs/intent.md`](docs/intent.md) | The founder's verbatim intent — authoritative |
| [`docs/interpreted-mvp.md`](docs/interpreted-mvp.md) | Structured reading of the intent |
| [`docs/design/OVERVIEW.md`](docs/design/OVERVIEW.md) | **Design entry point** — architecture, binding decisions, and links to the full spec (domain · API · frontend · deployment · build plan) |

**Implementing agent:** read `docs/design/OVERVIEW.md` and follow
`docs/design/BUILD_PLAN.md`. The design is decision-complete; when handed a
domain URL, `docs/design/DEPLOYMENT.md` § Runbook is the path to live.

Stack: FastAPI + Postgres (Docker) + Caddy auto-HTTPS + a no-build vanilla-JS PWA.
