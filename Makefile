.PHONY: install api web runner test

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -e packages/schemas/python -e apps/api[dev] -e apps/runner
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
