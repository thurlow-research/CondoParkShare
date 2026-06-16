#!/bin/bash
set -euo pipefail

# backup.sh — PostgreSQL backup for CondoParkShare
#
# Usage: ./scripts/backup.sh
#
# Environment variables:
#   DATABASE_URL                Required. PostgreSQL connection URL (postgres://user:pass@host/db)
#   BACKUP_ENCRYPTION_RECIPIENT Required. age public key for encrypting the backup archive
#   BACKUP_DIR                  Optional. Destination directory (default: /mnt/nas/backups/parkshare)
#
# The script creates a gzip-compressed, age-encrypted pg_dump and retains only the last 30 backups.
#
# Restore: age -d -i <private-key-path> <backup>.sql.gz.age | gunzip | psql "${DATABASE_URL}"

BACKUP_DIR="${BACKUP_DIR:-/mnt/nas/backups/parkshare}"
DATABASE_URL="${DATABASE_URL:?DATABASE_URL environment variable is required}"
BACKUP_ENCRYPTION_RECIPIENT="${BACKUP_ENCRYPTION_RECIPIENT:?BACKUP_ENCRYPTION_RECIPIENT environment variable is required}"

# Verify age is available before attempting the backup
command -v age >/dev/null 2>&1 || { echo "ERROR: age not found on PATH"; exit 1; }

TIMESTAMP="$(date +%Y%m%dT%H%M%S)"
FILENAME="parkshare_${TIMESTAMP}.sql.gz.age"

# Create backup directory if it does not exist
mkdir -p "${BACKUP_DIR}"

# Run pg_dump, compress, and encrypt — write atomically via a temp file so a
# mid-pipeline failure (disk full, network drop, bad key) never leaves a corrupt
# partial archive in the retention pool.
TMPFILE="${BACKUP_DIR}/${FILENAME}.tmp"
trap 'rm -f "${TMPFILE}"' ERR EXIT

echo "Starting backup: ${FILENAME}"
pg_dump "${DATABASE_URL}" | gzip | age -r "${BACKUP_ENCRYPTION_RECIPIENT}" > "${TMPFILE}"
mv "${TMPFILE}" "${BACKUP_DIR}/${FILENAME}"

trap - ERR EXIT
echo "Backup written to ${BACKUP_DIR}/${FILENAME}"

# Retain only the last 30 backups — delete older ones
BACKUP_COUNT="$(ls -1 "${BACKUP_DIR}"/parkshare_*.sql.gz.age 2>/dev/null | wc -l)"
if [ "${BACKUP_COUNT}" -gt 30 ]; then
    TO_DELETE="$(( BACKUP_COUNT - 30 ))"
    echo "Pruning ${TO_DELETE} old backup(s) (retaining last 30)"
    ls -1t "${BACKUP_DIR}"/parkshare_*.sql.gz.age | tail -n "${TO_DELETE}" | xargs rm -f
fi

echo "Backup complete. Total backups retained: $(ls -1 "${BACKUP_DIR}"/parkshare_*.sql.gz.age 2>/dev/null | wc -l)"
