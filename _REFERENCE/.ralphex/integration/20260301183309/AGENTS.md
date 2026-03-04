# Repository Guidelines

## Project Structure & Module Organization
VoiceVault is a small monorepo with Python backend and React frontend apps.
- `apps/api/`: FastAPI service, SQLAlchemy models, Alembic migrations, and backend tests (`apps/api/tests/`).
- `apps/web/`: Vite + React + TypeScript client (`src/` for UI code and styles).
- `infra/`: local infrastructure config (`docker-compose.dev.yml`).
- `docs/`: process and quality docs (for example, definition of done).
- Root files: `Makefile`, `pyproject.toml`, `.env.example`, and task/planning docs.

## Build, Test, and Development Commands
Use `uv` for all Python package/runtime operations.
- Install/remove Python packages: `uv add <pkg>` / `uv remove <pkg>` (do not use `pip install` directly).
- Run Python entrypoints/scripts/tests: `uv run python main.py`, `uv run python -m unittest discover -s apps/api/tests`.
- Frontend dev server: `cd apps/web && npm run dev`.
- Frontend production build: `cd apps/web && npm run build`.
- Backend migrations (Docker): `docker compose run --rm api sh -lc 'cd /app/apps/api && alembic upgrade head'`.
- Make targets (`make dev-up`, `make dev-down`) are helper wrappers; verify their behavior before relying on them in CI.

## Coding Style & Naming Conventions
- Python: 4-space indentation, type hints for new/changed functions, `snake_case` for functions/modules, `PascalCase` for classes.
- TypeScript/React: component files in `PascalCase` (for example, `App.tsx`), hooks/utilities in `camelCase`.
- Keep modules focused; colocate tests under each app’s `tests/` folder.
- Prefer small, explicit functions over broad utility files.

## Testing Guidelines
- Backend tests use `unittest`; test files follow `test_*.py`.
- Run backend tests with `uv run python -m unittest discover -s apps/api/tests`.
- Frontend test tooling is not yet configured; when adding it, place tests next to components or under `apps/web/src/__tests__/`.
- For backend changes, include at least one test covering new behavior or migration wiring.

## Commit & Pull Request Guidelines
- Follow existing commit style: `<scope>: <imperative summary>` (examples: `apps/web: add Vite+React skeleton`, `docs: add env example`).
- Keep commits focused and avoid mixing unrelated changes.
- PRs should include: purpose, affected areas (`apps/api`, `apps/web`, `infra`), test evidence (commands + results), and screenshots for UI changes.
- Link related tasks/issues when available and call out config/env changes explicitly.
