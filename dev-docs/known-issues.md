# Known issues

Open behavior gaps that reviewers, release gates, and the downstream smoke should know about, so adversarial passes do not re-report them as new. Each entry says since when it holds and where it is tracked. Remove an entry in the PR that fixes it.

| issue | since | tracked |
|---|---|---|
| A FastMCP proxy in front of a stdio backend relays progress but drops the backend's log messages. | at least 4.0.5 | `PROXY_GAPS` in `tests/downstream/_harness.py` |
| A handshake-era (≤ 2025-11-25) client gets no elicitation through a proxy whose backend negotiated 2026-07-28: the proxy has no back-channel to relay `elicitation/create`. | at least 4.0.5 | `LEGACY_PROXY_GAPS` in `tests/downstream/_harness.py` |
| A mount with a non-ASCII namespace lists resources and templates that cannot be read: clients send the namespaced URI percent-encoded. | at least 4.0.5 | — |
| A proxy percent-encodes template literals outside RFC 3986 (such as a pipe or a bare `%`) when it rebuilds the backend URI, so a backend that matches those literals only as written (FastMCP ≤ 4.0.5) cannot find the resource. | #5167 | — |
| A stdio client whose exit is cancelled right before `asyncio.run` ends has its session stop cut off by loop shutdown, and every connect in a later `asyncio.run` fails with "Event loop is closed". | at least 4.0.5 | — |
| langchain 1.4.2's `test_separate_adapters_keep_their_own_protocol_era` fails against every fastmcp 4.0.x: it asserts on the caller's `Client` while `MCPAdapter` connects a clone. Upstream test bug. | langchain 1.4.2 | deselected in `.github/workflows/run-downstream-smoke.yml` |
