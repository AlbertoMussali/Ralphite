.PHONY: install api web runner test init doctor run history replay migrate check tui

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -e packages/schemas/python -e packages/engine -e apps/tui -e apps/api[dev] -e apps/runner
	corepack enable
	corepack prepare pnpm@9.12.0 --activate
	pnpm install

api:
	. .venv/bin/activate && uvicorn ralphite_api.main:app --reload --port 8000

web:
	pnpm --filter @ralphite/web dev

runner:
	. .venv/bin/activate && ralphite-runner --api-base http://localhost:8000 --workspace-root $(WORKSPACE_ROOT)

test:
	. .venv/bin/activate && PYTHONPATH=apps/api/src:packages/schemas/python/src pytest apps/api/tests -q

init:
	uv run ralphite init --workspace $(WORKSPACE_ROOT)

doctor:
	uv run ralphite doctor --workspace $(WORKSPACE_ROOT)

run:
	uv run ralphite run --workspace $(WORKSPACE_ROOT)

history:
	uv run ralphite history --workspace $(WORKSPACE_ROOT)

replay:
	uv run ralphite replay $(RUN_ID) --workspace $(WORKSPACE_ROOT)

migrate:
	uv run ralphite migrate --workspace $(WORKSPACE_ROOT)

check:
	uv run ralphite check --workspace $(WORKSPACE_ROOT) --full

tui:
	uv run ralphite tui --workspace $(WORKSPACE_ROOT)
