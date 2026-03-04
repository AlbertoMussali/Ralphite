.PHONY: dev-up dev-down lint test

dev-up:
	@if [ -f docker-compose.yml ] || [ -f compose.yml ] || [ -f compose.yaml ]; then \
		docker compose up -d; \
	else \
		echo "No Docker Compose file found; skipping dev-up."; \
	fi

dev-down:
	@if [ -f docker-compose.yml ] || [ -f compose.yml ] || [ -f compose.yaml ]; then \
		docker compose down; \
	else \
		echo "No Docker Compose file found; skipping dev-down."; \
	fi

lint:
	@echo "No lint configuration found; lint target is a no-op."

test:
	UV_CACHE_DIR=/tmp/uv-cache uv run python -m unittest discover -s apps/api/tests
