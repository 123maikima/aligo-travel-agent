FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    REDIS_HOST=redis \
    REDIS_PORT=6379 \
    REDIS_ENABLED=true \
    POSTGRES_HOST=postgres \
    POSTGRES_PORT=5432 \
    POSTGRES_DB=travel_agent \
    POSTGRES_USER=postgres \
    POSTGRES_PASSWORD=postgres \
    POSTGRES_SSLMODE=disable \
    POSTGRES_ENABLED=true

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml /app/
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . /app
RUN chmod +x /app/docker-entrypoint.sh

RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "cli.py"]
