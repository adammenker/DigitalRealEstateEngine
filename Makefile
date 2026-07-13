.PHONY: verify backend-check frontend-build docker-build

verify: backend-check frontend-build docker-build

backend-check:
	python3 -m ruff check src tests
	python3 -m mypy src
	python3 -m pytest -q

frontend-build:
	docker run --rm -v "$$PWD/frontend:/app" -w /app node:20-slim sh -lc "npm ci && npm run build"

docker-build:
	docker compose build

