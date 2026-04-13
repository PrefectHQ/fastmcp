# Keycloak OAuth Example

Demonstrates FastMCP server protection with Keycloak OAuth.

**Requires Keycloak 26.6.0 or later** with Dynamic Client Registration enabled.

## Setup

1. Configure a Keycloak realm with Dynamic Client Registration enabled and a trusted host policy for your server URL (e.g. `http://127.0.0.1:8000/*`).

2. Set environment variables:

   ```bash
   export KEYCLOAK_REALM_URL="http://localhost:8080/realms/your-realm"
   ```

3. Run the server:

   ```bash
   python server.py
   ```

4. In another terminal, run the client:

   ```bash
   python client.py
   ```

The client will open your browser for Keycloak authentication.
