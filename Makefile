.DEFAULT_GOAL := check

BASE ?= origin/main
GITLEAKS ?= gitleaks

.PHONY: check test web-check web-e2e pre-push release-check operational-test

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src
	uv run python -m pytest -m unit

test: check
	uv run python scripts/clean_coverage.py
	uv run python -m pytest --cov --cov-branch --cov-report=xml --cov-fail-under=80

web-check:
	npm --prefix web run typecheck
	npm --prefix web run test
	npm --prefix web run build

web-e2e:
	npm --prefix web run e2e
	npm --prefix web run performance

pre-push: test web-check
	uv run diff-cover coverage.xml --compare-branch=$(BASE) --fail-under=80
	$(GITLEAKS) detect --redact --no-banner --source .
	uv run python scripts/floor_guard.py --base $(BASE)

release-check: pre-push web-e2e
	uv run python scripts/run_available_tests.py --markers "api or integration or retrieval_eval or answer_eval or agentic or security"

operational-test:
	uv run python scripts/run_available_tests.py --markers operational
