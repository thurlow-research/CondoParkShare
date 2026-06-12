# CondoParkShare — Production Crontab and Deployment Guide

## Cron jobs (opus)

Four cron jobs are registered on the production host (opus). All management
commands run inside the `web` container via `docker exec`.

```cron
# CondoParkShare scheduled tasks
# Notify borrowers/owners: booking starts and completions (runs at :00)
0  * * * *  docker exec web python manage.py notify_bookings --event=starts,completions

# Notify borrowers/owners: 30-minute warning (runs at :30)
30 * * * *  docker exec web python manage.py notify_bookings --event=warning_30

# Notify borrowers/owners: 15-minute warning (runs at :45)
45 * * * *  docker exec web python manage.py notify_bookings --event=warning_15

# Database backup with 30-backup retention (runs daily at 02:00)
0  2 * * *  /opt/parkshare/scripts/backup.sh >> /var/log/parkshare/backup.log 2>&1
```

### Running management commands manually

```bash
docker exec web python manage.py <command> [options]
```

Examples:

```bash
# Run migrations
docker exec web python manage.py migrate

# Collect static files
docker exec web python manage.py collectstatic --no-input

# Open a Django shell
docker exec -it web python manage.py shell
```

---

## Required environment variables

All variables must be present in `/opt/parkshare/.env` on the production host.
The file must be readable only by the user running Docker Compose (`chmod 600`).

| Variable | Description |
|---|---|
| `SECRET_KEY` | Django secret key — generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `DEBUG` | Must be `False` in production |
| `ALLOWED_HOSTS` | Comma-separated list, e.g. `parkshare.kumajyo.com,parkshare.bellevuetowers.org` |
| `DATABASE_URL` | PostgreSQL connection URL, e.g. `postgres://parkshare:password@db/parkshare` |
| `PII_ENCRYPTION_KEY` | 32-byte url-safe base64 Fernet key for field-level PII encryption |
| `BREVO_API_KEY` | Brevo (Sendinblue) transactional email API key |
| `EMAIL_BACKEND` | Default: `anymail.backends.brevo.EmailBackend` |
| `DEFAULT_FROM_EMAIL` | From address for outbound email, e.g. `noreply@parkshare.kumajyo.com` |
| `VAPID_PRIVATE_KEY` | VAPID private key for web push notifications |
| `VAPID_PUBLIC_KEY` | VAPID public key for web push notifications |
| `VAPID_ADMIN_EMAIL` | Admin email for VAPID contact header |
| `BACKUP_DIR` | Backup destination directory (default: `/mnt/nas/backups/parkshare`) |

---

## Deploy steps

```bash
# 1. Pull latest code
cd /opt/parkshare
git pull

# 2. Rebuild and restart containers (--build forces image rebuild)
docker compose up -d --build

# 3. Run database migrations
docker exec web python manage.py migrate

# 4. Collect static files (requires env vars; run after containers are up)
docker exec web python manage.py collectstatic --no-input
```

### First-time setup only

```bash
# Generate VAPID keys (run once, store in .env)
docker exec web python -c "
from py_vapid import Vapid
v = Vapid()
v.generate_keys()
print('VAPID_PRIVATE_KEY=' + v.private_key.private_bytes(
    encoding=__import__('cryptography').hazmat.primitives.serialization.Encoding.PEM,
    format=__import__('cryptography').hazmat.primitives.serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=__import__('cryptography').hazmat.primitives.serialization.NoEncryption(),
).decode())
"

# Create superuser
docker exec -it web python manage.py createsuperuser
```
