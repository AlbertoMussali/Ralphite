.PHONY: install test init doctor run recover history replay check tui

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -e packages/schemas/python -e packages/engine -e apps/tui

init:
	uv run ralphite init --workspace $(WORKSPACE_ROOT)

doctor:
	uv run ralphite doctor --workspace $(WORKSPACE_ROOT)

run:
	uv run ralphite run --workspace $(WORKSPACE_ROOT)

recover:
	uv run ralphite recover --workspace $(WORKSPACE_ROOT)

history:
	uv run ralphite history --workspace $(WORKSPACE_ROOT)

replay:
	uv run ralphite replay $(RUN_ID) --workspace $(WORKSPACE_ROOT)

check:
	uv run ralphite check --workspace $(WORKSPACE_ROOT) --full

tui:
	uv run ralphite tui --workspace $(WORKSPACE_ROOT)
