#!/bin/bash
# Dump the production database. Run on the VM (cron-able). Keeps 14 days.
#   0 3 * * * /opt/active-impact/scripts/backup.sh
set -e
cd "$(dirname "$0")/.."
mkdir -p backups
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U postgres impact > "backups/impact-$(date +%F).sql"
find backups -name 'impact-*.sql' -mtime +14 -delete
echo "backup written: backups/impact-$(date +%F).sql"
