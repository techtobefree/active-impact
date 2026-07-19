#!/bin/bash
# Deploy Active Impact to the server in .env — the "update the web" command.
#
#   ./deploy.sh
#
# Reads DEPLOY_HOST / SITE_ADDRESS / POSTGRES_PASSWORD from .env (gitignored),
# ships the current tree + .env over SSH, rebuilds the prod stack on the server,
# waits for health, and smoke-tests it in place. Idempotent — run it any time.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo "!! no .env (needs DEPLOY_HOST, SITE_ADDRESS, POSTGRES_PASSWORD)"; exit 1; }
# shellcheck disable=SC1091
set -a; . ./.env; set +a
: "${DEPLOY_HOST:?set DEPLOY_HOST in .env, e.g. root@1.2.3.4}"
REMOTE_DIR="${REMOTE_DIR:-/opt/active-impact}"
SSH="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20"
SCP="scp -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20"
COMPOSE="docker compose -f docker-compose.prod.yml"

echo "[1/5] packaging (+ .env, minus local/dev cruft)..."
TARBALL="$(mktemp /tmp/active-impact-XXXXXX.tgz)"
tar czf "$TARBALL" \
  --exclude='.git' --exclude='.venv' --exclude='node_modules' \
  --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.pgdata' \
  --exclude='screenshots' --exclude='test-results' --exclude='playwright-report' \
  --exclude='backups' --exclude='*.tgz' \
  -C . .
echo "     tarball: $(du -h "$TARBALL" | cut -f1)"

echo "[2/5] shipping to $DEPLOY_HOST:$REMOTE_DIR ..."
$SSH "$DEPLOY_HOST" "mkdir -p '$REMOTE_DIR'"
$SCP -q "$TARBALL" "$DEPLOY_HOST:$REMOTE_DIR/_deploy.tgz"
rm -f "$TARBALL"

echo "[3/5] extract + build + up (first build pulls images + pip — a few minutes)..."
$SSH "$DEPLOY_HOST" "cd '$REMOTE_DIR' && tar -xzf _deploy.tgz && rm -f _deploy.tgz && $COMPOSE up -d --build"

echo "[4/5] waiting for the app to become healthy..."
$SSH "$DEPLOY_HOST" "cd '$REMOTE_DIR' && for i in \$(seq 1 45); do $COMPOSE exec -T app python -c 'import urllib.request as u; u.urlopen(\"http://localhost:8000/api/health\",timeout=3)' 2>/dev/null && { echo '     healthy'; exit 0; }; sleep 2; done; echo '!! not healthy'; $COMPOSE logs --tail=40 app; exit 1"

echo "[5/5] smoke test in place..."
$SSH "$DEPLOY_HOST" "cd '$REMOTE_DIR' && $COMPOSE exec -T app python scripts/smoke.py http://localhost:8000"

echo ""
echo "✅ Deployed to ${DEPLOY_HOST#*@}. SITE_ADDRESS=${SITE_ADDRESS}"
