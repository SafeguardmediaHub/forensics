.PHONY: install install-api install-engines dev run worker beat redis smoke-app smoke-http test lint format check clean

PYTHON     := python3
UVICORN    := PYTHONPATH=src uvicorn api.main:app
CELERY     := PYTHONPATH=src celery -A api.workers.celery_app worker
PYTEST     := PYTHONPATH=src pytest

# ── Setup ─────────────────────────────────────────────────────────────────────

install:
	$(PYTHON) -m pip install -r requirements-engines.txt
	$(PYTHON) -m pip install -e ".[dev]"

install-api:
	$(PYTHON) -m pip install -e ".[dev]"

install-engines:
	$(PYTHON) -m pip install -r requirements-engines.txt

# ── Run ───────────────────────────────────────────────────────────────────────

run:
	$(UVICORN) --host 0.0.0.0 --port 8000 --reload

worker:
	$(CELERY) --loglevel=info

beat:
	PYTHONPATH=src celery -A api.workers.celery_app beat --loglevel=info

redis:
	redis-server --save "" --appendonly no --port 6379

smoke-app:
	PYTHONPATH=src $(PYTHON) scripts/smoke_app.py

smoke-http:
	PYTHONPATH=src $(PYTHON) scripts/smoke_http.py --base-url http://127.0.0.1:8000

# ── Test ──────────────────────────────────────────────────────────────────────

test:
	$(PYTEST) -v

test-cov:
	$(PYTEST) -v --cov=api --cov-report=term-missing

# ── Lint & Format ─────────────────────────────────────────────────────────────

lint:
	PYTHONPATH=src ruff check src/ tests/

format:
	black src/ tests/

check: lint
	black --check src/ tests/

# ── Clean ─────────────────────────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
