# FastMCP Contrib Modules

This directory holds community-contributed modules for FastMCP. These modules extend FastMCP's functionality but are not officially maintained by the core team.

**Guarantees:**
*   Modules in `contrib` may have different testing requirements or stability guarantees compared to the core library.
*   Changes to the core FastMCP library might break modules in `contrib` without explicit warnings in the main changelog.

Use these modules at your own discretion. Contributions are welcome, but please include tests and documentation.

## Usage

To use a contrib module, import it from the `fastmcp.contrib` package.

```python
from fastmcp.contrib import my_module
```

Note that the contrib modules may have different dependencies than the core library, which can be noted in their respective README's or even separate requirements / dependency files.