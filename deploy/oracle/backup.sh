#!/usr/bin/env bash
# Nightly MySQL dump, keeps 14 days. Cron:  0 2 * * * /home/ubuntu/parkshare/deploy/oracle/backup.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p backups
docker compose -f docker-compose.prod.yml exec -T db sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --no-tablespaces "$MYSQL_DATABASE"' | gzip > "backups/parkshare-$(date +%F).sql.gz"
find backups -name 'parkshare-*.sql.gz' -mtime +14 -delete
