.DEFAULT_GOAL := check

BASE ?= origin/main
GITLEAKS ?= gitleaks

.PHONY: check test pre-push release-check operational-test

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src
	uv run pytest -m unit

test: check
	uv run python scripts/clean_coverage.py
	uv run pytest --cov --cov-branch --cov-report=xml --cov-fail-under=80

pre-push: test
	uv run diff-cover coverage.xml --compare-branch=$(BASE) --fail-under=80
	$(GITLEAKS) detect --redact --no-banner --source .
	uv run python scripts/floor_guard.py --base $(BASE)

release-check: pre-push
	uv run python scripts/run_available_tests.py --markers "api or integration or retrieval_eval or answer_eval or agentic or security"

operational-test:
	uv run python scripts/run_available_tests.py --markers operational
