build:
    uv sync

test: build
    uv run --frozen pytest -xvs tests

# Run pyright on all files
typecheck:
    uv run --frozen pyright