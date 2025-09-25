# AWS Cognito OAuth Example

Demonstrates FastMCP server protection with AWS Cognito OAuth.

## Setup

1. Create an AWS Cognito User Pool and App Client:
   - Go to [AWS Cognito Console](https://console.aws.amazon.com/cognito/)
   - Create a new User Pool or use an existing one
   - Create an App Client in your User Pool
   - Configure the App Client settings:
     - Enable "Authorization code grant" flow
     - Add Callback URL: `http://localhost:8000/auth/callback`
     - Configure OAuth scopes (at minimum: `openid`)
   - Note your User Pool ID, App Client ID, Client Secret, and Cognito Domain Prefix

2. Set environment variables:

   ```bash
   export FASTMCP_SERVER_AUTH_AWS_COGNITO_USER_POOL_ID="your-user-pool-id"
   export FASTMCP_SERVER_AUTH_AWS_COGNITO_AWS_REGION="your-aws-region"
   export FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_ID="your-app-client-id"
   export FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_SECRET="your-app-client-secret"
   ```

   Or create a `.env` file:

   ```env
   FASTMCP_SERVER_AUTH_AWS_COGNITO_USER_POOL_ID=your-user-pool-id
   FASTMCP_SERVER_AUTH_AWS_COGNITO_AWS_REGION=your-aws-region
   FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_ID=your-app-client-id
   FASTMCP_SERVER_AUTH_AWS_COGNITO_CLIENT_SECRET=your-app-client-secret
   ```

3. Run the server:

   ```bash
   python server.py
   ```

4. In another terminal, run the client:

   ```bash
   python client.py
   ```

The client will open your browser for AWS Cognito authentication.
