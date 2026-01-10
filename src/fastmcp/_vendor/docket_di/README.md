# Vendored Docket DI

This is a minimal vendored copy of the dependency injection engine from [Docket](https://github.com/chrisguidry/docket), pending its release as a standalone library.

When `fastmcp[tasks]` is installed, FastMCP uses Docket's DI classes directly for `isinstance` compatibility in worker contexts. This vendored version is only used when Docket is not installed, allowing basic `Depends()` functionality without the full Docket dependency.

Once the DI component is released separately, this vendored copy will be removed.
