.PHONY: install test init doctor run recover history replay check

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -e .

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
