#!/bin/bash
# Ship Active Impact to a server and bring up the production stack.
# Tarball over SSH -- no git on the server.
#
#   ./deploy.sh user@host [/remote/dir]
#
# NOTE: extraction overwrites in place and never prunes -- renamed/deleted files
# linger on the server. First run fails at the .env guard by design: SSH in,
# create .env (see DEPLOY.md / .env.example), then re-run.
set -e

DEST="$1"
REMOTE_DIR="${2:-/opt/active-impact}"
if [ -z "$DEST" ]; then
  echo "usage: ./deploy.sh user@host [/remote/dir]"
  exit 1
fi

echo "[1/4] Packaging source..."
TARBALL=$(mktemp /tmp/active-impact-XXXX.tgz)
tar --exclude='./.git' --exclude='./.venv' --exclude='./__pycache__' \
    --exclude='./.pytest_cache' --exclude='./*.tgz' --exclude='./node_modules' \
    -czf "$TARBALL" -C "$(dirname "$0")" .

echo "[2/4] Copying to $DEST:$REMOTE_DIR ..."
ssh "$DEST" "mkdir -p '$REMOTE_DIR'"
scp "$TARBALL" "$DEST:$REMOTE_DIR/_deploy.tgz"
rm -f "$TARBALL"

echo "[3/4] Extracting on server..."
ssh "$DEST" "cd '$REMOTE_DIR' && tar -xzf _deploy.tgz && rm -f _deploy.tgz"

echo "[4/4] Building and starting the stack..."
ssh "$DEST" "cd '$REMOTE_DIR' && \
  if [ ! -f .env ]; then echo '!! No .env on server -- copy .env.example to .env and set secrets, then re-run.'; exit 1; fi && \
  docker compose -f docker-compose.prod.yml up -d --build"

echo "Done. Check: ssh $DEST 'cd $REMOTE_DIR && docker compose -f docker-compose.prod.yml ps'"
