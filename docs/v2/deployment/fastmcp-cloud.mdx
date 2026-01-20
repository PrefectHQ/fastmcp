---
title: FastMCP Cloud
sidebarTitle: FastMCP Cloud
description: The fastest way to deploy your MCP server
icon: cloud
tag: NEW
---

[FastMCP Cloud](https://fastmcp.cloud) is a managed platform for hosting MCP servers, built by the FastMCP team. While the FastMCP framework will always be fully open-source, we created FastMCP Cloud to solve the deployment challenges we've seen developers face. Our goal is to provide the absolute fastest way to make your MCP server available to LLM clients like Claude and Cursor. 

FastMCP Cloud is a young product and we welcome your feedback. Please join our [Discord](https://discord.com/invite/aGsSC3yDF4) to share your thoughts and ideas, and you can expect to see new features and improvements every week.


<Note>
FastMCP Cloud supports both **FastMCP 2.0** servers and also **FastMCP 1.0** servers that were created with the official MCP Python SDK.
</Note>

<Tip>
FastMCP Cloud is completely free while in beta!
</Tip>

## Prerequisites

To use FastMCP Cloud, you'll need a [GitHub](https://github.com) account. In addition, you'll need a GitHub repo that contains a FastMCP server instance. If you don't want to create one yet, you can proceed to [step 1](#step-1-create-a-project) and use the FastMCP Cloud quickstart repo. 

Your repo can be public or private, but must include at least a Python file that contains a FastMCP server instance. 
<Tip>
To ensure your file is compatible with FastMCP Cloud, you can run `fastmcp inspect <file.py:server_object>` to see what FastMCP Cloud will see when it runs your server.
</Tip>

If you have a `requirements.txt` or `pyproject.toml` in the repo, FastMCP Cloud will automatically detect your server's dependencies and install them for you. Note that your file *can* have an `if __name__ == "__main__"` block, but it will be ignored by FastMCP Cloud.

For example, a minimal server file might look like:

```python
from fastmcp import FastMCP

mcp = FastMCP("MyServer")

@mcp.tool
def hello(name: str) -> str:
    return f"Hello, {name}!"
```

## Getting Started

There are just three steps to deploying a server to FastMCP Cloud:

### Step 1: Create a Project

Visit [fastmcp.cloud](https://fastmcp.cloud) and sign in with your GitHub account. Then, create a project. Each project corresponds to a GitHub repo, and you can create one from either your own repo or using the FastMCP Cloud quickstart repo.

<img src="/assets/images/fastmcp_cloud/quickstart.png" alt="FastMCP Cloud Quickstart Screen" />

Next, you'll be prompted to configure your project.

<img src="/assets/images/fastmcp_cloud/create_project.png" alt="FastMCP Cloud Configuration Screen" />

The configuration screen lets you specify:
- **Name**: The name of your project. This will be used to generate a unique URL for your server.
- **Entrypoint**: The Python file containing your FastMCP server (e.g., `echo.py`). This field has the same syntax as the `fastmcp run` command, for example `echo.py:my_server` to specify a specific object in the file.
- **Authentication**: If disabled, your server is open to the public. If enabled, only other members of your FastMCP Cloud organization will be able to connect.

Note that FastMCP Cloud will automatically detect yours server's Python dependencies from either a `requirements.txt` or `pyproject.toml` file.

### Step 2: Deploy Your Server

Once you configure your project, FastMCP Cloud will:
1. Clone the repository
2. Build your FastMCP server
3. Deploy it to a unique URL
4. Make it immediately available for connections

<img src="/assets/images/fastmcp_cloud/deployment.png" alt="FastMCP Cloud Deployment Screen" />

FastMCP Cloud will monitor your repo and redeploy your server whenever you push a change to the `main` branch. In addition, FastMCP Cloud will build and deploy servers for every PR your open, hosting them on unique URLs, so you can test changes before updating your production server.

### Step 3: Connect to Your Server

Once your server is deployed, it will be accessible at a URL like:

```
https://your-project-name.fastmcp.app/mcp
```

You should be able to connect to it as soon as you see the deployment succeed! FastMCP Cloud provides instant connection options for popular LLM clients:

<img src="/assets/images/fastmcp_cloud/connect.png" alt="FastMCP Cloud Connection Screen" />

