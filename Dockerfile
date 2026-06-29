# Pinned to the multi-arch index digest for reproducible builds (CPS#165).
# Mutable tags (python:3.12-slim) drift; bump this digest deliberately when
# rolling base-image updates. Resolve a new digest with:
#   docker buildx imagetools inspect python:3.12-slim
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=parkshare.settings.production

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Run as an unprivileged user (CPS#165). gunicorn binds :8000 (>1024) so no
# root is needed. /app/logs is the mountpoint for the audit_logs named volume;
# creating + chowning it here seeds the volume's initial ownership to appuser
# on first creation, so the JSONL recovery sink stays writable without root.
RUN useradd --uid 1001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/logs \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# collectstatic is NOT run here — it requires a DB connection / env vars.
# Run it separately on deploy: docker exec web python manage.py collectstatic --no-input

# Worker count is owned by docker-compose.yml's command override (CPS#165), which
# is authoritative; this CMD is a single-worker fallback for bare `docker run`.
CMD ["gunicorn", "parkshare.wsgi:application", "--bind", "0.0.0.0:8000"]
