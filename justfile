# Build the project
build:
    uv sync

# Run tests
test: build
    uv run --frozen pytest -xvs tests

# Run pyright on all files
typecheck:
    uv run --frozen pyright

# Serve documentation locally
docs:
    cd docs && npx mint@latest dev

# Generate API reference documentation for all modules
api-ref-all:
    uvx --with-editable . --refresh-package mdxify mdxify@latest --all --root-module fastmcp --anchor-name "SDK Reference"

# Generate API reference for specific modules (e.g., just api-ref prefect.flows prefect.tasks)
api-ref *MODULES:
    uvx --with-editable . --refresh-package mdxify mdxify@latest {{MODULES}} --root-module fastmcp --anchor-name "SDK Reference"

# Clean up API reference documentation
api-ref-clean:
    rm -rf docs/python-sdk