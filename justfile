api_ref_package := "fastmcp-slim[anthropic,apps,azure,code-mode,full,gemini,openai,tasks] @ file://" + justfile_directory() + "/fastmcp_slim"

# Build the project
build:
    uv sync

# Run tests
test: build
    uv run --frozen pytest -xvs tests

# Run ty type checker on all files
typecheck:
    uv run --frozen ty check

# Serve documentation locally
docs:
    cd docs && npx --yes mint@latest dev

# Check for broken links in documentation
docs-broken-links:
    cd docs && npx --yes mint@latest broken-links

# Generate API reference documentation for all modules
api-ref-all:
    uvx --with-editable "{{api_ref_package}}" --refresh-package mdxify mdxify@latest --all --root-module fastmcp --nav-output docs/python-sdk-pages.json --exclude fastmcp.contrib
# Generate API reference for specific modules (e.g., just api-ref prefect.flows prefect.tasks)
api-ref *MODULES:
    uvx --with-editable "{{api_ref_package}}" --refresh-package mdxify mdxify@latest {{MODULES}} --root-module fastmcp --nav-output docs/python-sdk-pages.json

# Clean up API reference documentation
api-ref-clean:
    rm -rf docs/python-sdk

copy-context:
    uvx --with-editable fastmcp_slim --refresh-package copychat copychat@latest fastmcp_slim/fastmcp docs/ -x changelog.mdx -x python-sdk/ -v
