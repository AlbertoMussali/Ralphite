# API Service

## Database migrations

The API and Alembic read the same `DATABASE_URL` setting (`app/settings.py`).

Run migrations against the Docker Compose Postgres service:

```bash
docker compose run --rm api sh -lc 'cd /app/apps/api && alembic upgrade head'
```

## Background jobs (RQ)

Run a local worker from the compose stack:

```bash
docker compose -f infra/docker-compose.dev.yml up worker redis
```

Enqueue the built-in stub job:

```bash
docker compose -f infra/docker-compose.dev.yml run --rm worker sh -lc 'cd /workspace/apps/api && uv run --with-requirements requirements.txt python -m app.enqueue_stub_job'
```
