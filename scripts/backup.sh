#!/bin/bash
set -euo pipefail

# backup.sh — PostgreSQL backup for CondoParkShare
#
# Usage: ./scripts/backup.sh
#
# Environment variables:
#   DATABASE_URL   Required. PostgreSQL connection URL (postgres://user:pass@host/db)
#   BACKUP_DIR     Optional. Destination directory (default: /mnt/nas/backups/parkshare)
#
# The script creates a gzip-compressed pg_dump and retains only the last 30 backups.

BACKUP_DIR="${BACKUP_DIR:-/mnt/nas/backups/parkshare}"
DATABASE_URL="${DATABASE_URL:?DATABASE_URL environment variable is required}"

TIMESTAMP="$(date +%Y%m%dT%H%M%S)"
FILENAME="parkshare_${TIMESTAMP}.sql.gz"

# Create backup directory if it does not exist
mkdir -p "${BACKUP_DIR}"

# Run pg_dump and gzip output
echo "Starting backup: ${FILENAME}"
pg_dump "${DATABASE_URL}" | gzip > "${BACKUP_DIR}/${FILENAME}"

echo "Backup written to ${BACKUP_DIR}/${FILENAME}"

# Retain only the last 30 backups — delete older ones
BACKUP_COUNT="$(ls -1 "${BACKUP_DIR}"/parkshare_*.sql.gz 2>/dev/null | wc -l)"
if [ "${BACKUP_COUNT}" -gt 30 ]; then
    TO_DELETE="$(( BACKUP_COUNT - 30 ))"
    echo "Pruning ${TO_DELETE} old backup(s) (retaining last 30)"
    ls -1t "${BACKUP_DIR}"/parkshare_*.sql.gz | tail -n "${TO_DELETE}" | xargs rm -f
fi

echo "Backup complete. Total backups retained: $(ls -1 "${BACKUP_DIR}"/parkshare_*.sql.gz 2>/dev/null | wc -l)"
