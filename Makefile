.PHONY: verify

verify:
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest
