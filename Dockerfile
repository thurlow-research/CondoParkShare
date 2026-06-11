FROM python:3.12-slim

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

# Collect static files (requires env vars to be set at build time or use --no-input)
RUN python manage.py collectstatic --no-input || true

EXPOSE 8000

CMD ["gunicorn", "parkshare.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4"]
