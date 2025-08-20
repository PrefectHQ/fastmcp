# Azure (Microsoft Entra) OAuth Example

This example demonstrates how to use the Azure OAuth provider with FastMCP servers.

## Setup

### 1. Azure App Registration

1. Go to [Azure Portal → App registrations](https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click "New registration" and configure:
   - Name: Your app name
   - Supported account types: Choose based on your needs
   - Redirect URI: `http://localhost:8000/auth/callback` (Web platform)
3. After creation, go to "Certificates & secrets" → "New client secret"
4. Note these values from the Overview page:
   - Application (client) ID
   - Directory (tenant) ID

### 2. Environment Variables

Create a `.env` file:

```bash
# Required
AZURE_CLIENT_ID=your-application-client-id
AZURE_CLIENT_SECRET=your-client-secret-value
AZURE_TENANT_ID=your-tenant-id  # From Azure Portal Overview page
```

### 3. Run the Example

Start the server:

```bash
uv run python server.py
```

Test with client:

```bash
uv run python client.py
```

## Tenant Configuration

The `tenant_id` parameter is **required** and controls which accounts can authenticate:

- **Your tenant ID**: Single organization (most common)
- **`organizations`**: Any work/school account
- **`consumers`**: Personal Microsoft accounts only

