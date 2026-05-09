# Oracle (OCI IAM (Identity Domain)) OAuth Example

This example demonstrates how to use the OCI IAM OAuth provider with FastMCP servers.

## Setup

### 1. OCI App Registration

1. Login to OCI console (https://cloud.oracle.com for OCI commercial cloud).
2. From "Identity & Security" menu, open Domains page.
3. On the Domains list page, select the domain in which you want to create MCP server OAuth client. If you need help finding the list page for the domain, see [Listing Identity Domains.](https://docs.oracle.com/en-us/iaas/Content/Identity/domains/to-view-identity-domains.htm#view-identity-domains).
4. On the details page, select Integrated applications. A list of applications in the domain is displayed.
5. Select Add application.
6. In the Add application window, select Confidential Application.
7. Select Launch workflow.
8. In the Add application details page, Enter name and description and create the application.
9. Once the Integrated Application is created, Click on "OAuth configuration" tab.
10. Click on "Edit OAuth configuration" button.
11. Configure the application as OAuth client by selecting "Configure this application as a client now" radio button.
12. Select "Authorization code" grant type. If you are planning to use the same OAuth client application for token exchange, select "Client credentials" grant type as well. In the sample, we will use the same client.
13. For Authorization grant type, select redirect URL. In most cases, this will be the MCP server URL followed by "/auth/callback". For example http://localhost:8000/auth/callback
14. Click on "Submit" button to update OAuth configuration for the client application.
15. Make sure to Activate the client application.
16. Note down client ID and client secret for the application. You'll use these values when configuring the OCIProvider in the MCP server.

For details instructions with screenshots, please refer to [FastMCP OCI Provider Documentation](https://gofastmcp.com/integrations/oci).

### 2. Set Environment Variables

```bash
# Required
FASTMCP_SERVER_AUTH_IDCS_CLIENT_ID=your-application-client-id
FASTMCP_SERVER_AUTH_IDCS_CLIENT_SECRET=your-client-secret-value
FASTMCP_SERVER_AUTH_IDCS_DOMAIN=your-iam-domain-url  # IDCS domain URL  for example idcs-abscasdwdac3432rdwsda.identity.oraclecloud.com
```

### 3. Run the Example

Start the server:

```bash
python server.py
```

Test with client:

```bash
python client.py
```

When you run the client, it will open a browser on your machine to login to OCI IAM domain.
