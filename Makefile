.PHONY: verify backend-check calibration frontend-build docker-build security-check

verify: backend-check calibration frontend-build docker-build security-check

backend-check:
	python3 -m ruff check src tests scripts
	python3 -m mypy --strict src
	python3 -m pytest -q

calibration:
	PYTHONPATH=src python3 -m rank_rent.cli calibrate validate-config
	PYTHONPATH=src python3 -m rank_rent.cli calibrate run --no-save

frontend-build:
	docker run --rm -v "$$PWD/frontend:/app" -w /app node:20.19.4-bookworm-slim sh -lc "npm ci --ignore-scripts && npm run lint && npm run build"

docker-build:
	docker compose config --quiet
	docker compose build

security-check:
	python3 -m pip_audit
	python3 -m bandit -q -lll -r src -x tests
	python3 scripts/check_licenses.py
	cd frontend && npm audit --audit-level=critical
