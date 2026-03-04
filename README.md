# Ralphite

Ralphite is a configurable multi-agent orchestration platform with:

- `apps/web`: React + Vite control plane
- `apps/api`: FastAPI orchestration API
- `apps/runner`: local runner daemon
- `packages/schemas`: shared plan/event schemas for Python/TypeScript

## Quick start

### Requirements

- Python 3.12+
- Node 20+
- pnpm 9+

### Install frontend

```bash
pnpm install
```

### Install backend/runner (uv workspace)

```bash
uv sync --all-packages
```

### Run backend tests (uv)

```bash
PYTHONPATH="apps/api/src:packages/schemas/python/src" uv run pytest apps/api/tests -q
```

### Install backend/runner (pip alternative)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e packages/schemas/python
pip install -e apps/api
pip install -e apps/runner
```

### Run API

```bash
PYTHONPATH="apps/api/src:packages/schemas/python/src" uv run python -m uvicorn ralphite_api.main:app --reload --port 8000
```

or

```bash
uvicorn ralphite_api.main:app --reload --port 8000
```

### Run web

```bash
pnpm --filter @ralphite/web dev
```

### Run runner

```bash
PYTHONPATH="apps/runner/src:packages/schemas/python/src" uv run python -m ralphite_runner.main --api-base http://localhost:8000 --workspace-root /absolute/path/to/project
```

or

```bash
ralphite-runner --api-base http://localhost:8000 --workspace-root /absolute/path/to/project
```

## Default local URLs

- Web: http://localhost:5173
- API docs: http://localhost:8000/docs

## Launch All Local Services

Use the convenience launcher:

```bash
./launch-dev.sh
```

Options:

```bash
./launch-dev.sh --workspace-root /absolute/path/to/project
./launch-dev.sh --skip-sync --skip-pnpm-install
./launch-dev.sh --api-port 8001 --web-port 5174
```
