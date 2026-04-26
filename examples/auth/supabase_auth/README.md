
This walks you through getting a full MCP + Supabase Auth setup, using Email Magic Link as the default login method.

It's been tested on the following MCP Clients:

1. FastMCP Client

## Supabase Setup

### Step 1: Enable OAuth with DCR

In the Supabase Authentication settings, this setting must be turned on:

**Enable the Supabase OAuth Server**

and Dynamic Client Registration (DCR) must also be enabled:

**Allow Dynamic OAuth Apps**

Leave **Site URL** and **Authorization Path** as their default values.  

### Step 2: Migrate JWT Keys if needed, and rotate JWT keys

Under **Project Settings / JWT Keys ** if you see:

> Right now your project is using the legacy JWT secret.

You must upgrade to the newer keys, because it will use HS256 (shared secret) which will break the MCP auth handshake.  Instead it should use RS256 (asymmetric keys + JWKS).

If you enable the new type of JWT secrets, you must rotate your keys in order to activate it.

### Step 3:  Enable Supabase Magic Link auth

Enabled by default, nothing to do here.

### Step 4:  Collect settings needed for env vars

1. Supabase URL
2. Anon key

See below.

## Script Setup

### Pull uv deps

```
uv sync
```

### Setup Env Vars

Copy .env.template to .env
Set your env variables accordingly

```
SUPABASE_PROJECT_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
```

You can get these from the Supabase dashboard.

The rest of the env vars can be left as defaults.

## How To Test

First activate the `venv` by running:

```
$ source .venv/bin/activate
```

Then run the following scripts in different terminals.

### Step 1: Start MCP Server 

```
uv run fastmcp run hello_supabase.py --transport http --port 8000
```

### Step 2: Start Consent UI Server

```
uv run uvicorn consent_server:app --port 3000
```

### Step 3: Run FastMCP Client

```
python client.py
```

### Step 4: Click Allow in browser window

It should open a browser window with Allow / Deny buttons and some debugging information.  Hit "Allow".

### Step 5: Verify that it worked

1. You will see error in browser window.  I am not sure what's going on here.
2. In the FastMCP client logs, if it worked you should see: `🎉 Tool result: CallToolResult(content=[TextContent(type='text', text='Hello from Supabase-protected MCP server!', annotations=None, meta=None)], structured_content={'result': 'Hello from Supabase-protected MCP server!'}, meta={'fastmcp': {'wrap_result': True}}, data='Hello from Supabase-protected MCP server!', is_error=False)`

## Needs Review

1. Email Auth vs other auth methods as default?   (github?)
