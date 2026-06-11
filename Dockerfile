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

EXPOSE 8000

# collectstatic is NOT run here — it requires a DB connection / env vars.
# Run it separately on deploy: docker exec web python manage.py collectstatic --no-input

CMD ["gunicorn", "parkshare.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
