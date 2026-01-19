# Namespace Activation

Demonstrates session-specific visibility control using tags to organize tools into namespaces that can be activated on demand.

## Pattern

1. Tag tools with namespaces: `@server.tool(tags={"namespace:finance"})`
2. Globally disable namespaces: `server.disable(tags={"namespace:finance"})`
3. Provide activation tools that call `ctx.enable_components(tags={"namespace:finance"})`

Each session starts with only the activation tools visible. When a session calls an activation tool, that namespace becomes visible **only for that session**.

## Run

```bash
# Server
uv run python server.py

# Client (in another terminal)
uv run python client.py
```

## Example Output

```
Namespace Activation Demo

╭─────────────────── Initial Tools ───────────────────╮
│ activate_finance, activate_admin, deactivate_all    │
╰─────────────────────────────────────────────────────╯

→ Calling activate_finance()
  Finance tools activated
╭─────────────── After Activating Finance ────────────╮
│ analyze_portfolio, get_market_data, execute_trade,  │
│ activate_finance, activate_admin, deactivate_all    │
╰─────────────────────────────────────────────────────╯

→ Calling get_market_data(symbol='AAPL')
  {'symbol': 'AAPL', 'price': 150.25, 'change': '+2.5%'}

→ Calling activate_admin()
  Admin tools activated
╭────────────── After Activating Admin ───────────────╮
│ analyze_portfolio, get_market_data, execute_trade,  │
│ list_users, reset_user_password, activate_finance,  │
│ activate_admin, deactivate_all                      │
╰─────────────────────────────────────────────────────╯

→ Calling deactivate_all()
  All namespaces deactivated
╭────────────── After Deactivating All ───────────────╮
│ activate_finance, activate_admin, deactivate_all    │
╰─────────────────────────────────────────────────────╯
```
