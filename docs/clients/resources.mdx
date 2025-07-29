---
title: Resource Operations
sidebarTitle: Resources
description: Access static and templated resources from MCP servers.
icon: folder-open
---

import { VersionBadge } from '/snippets/version-badge.mdx'

<VersionBadge version="2.0.0" />

Resources are data sources exposed by MCP servers. They can be static files or dynamic templates that generate content based on parameters.

## Types of Resources

MCP servers expose two types of resources:

- **Static Resources**: Fixed content accessible via URI (e.g., configuration files, documentation)
- **Resource Templates**: Dynamic resources that accept parameters to generate content (e.g., API endpoints, database queries)

## Listing Resources

### Static Resources

Use `list_resources()` to retrieve all static resources available on the server:

```python
async with client:
    resources = await client.list_resources()
    # resources -> list[mcp.types.Resource]
    
    for resource in resources:
        print(f"Resource URI: {resource.uri}")
        print(f"Name: {resource.name}")
        print(f"Description: {resource.description}")
        print(f"MIME Type: {resource.mimeType}")
        # Access tags and other metadata
        if hasattr(resource, '_meta') and resource._meta:
            fastmcp_meta = resource._meta.get('_fastmcp', {})
            print(f"Tags: {fastmcp_meta.get('tags', [])}")
```

### Resource Templates

Use `list_resource_templates()` to retrieve available resource templates:

```python
async with client:
    templates = await client.list_resource_templates()
    # templates -> list[mcp.types.ResourceTemplate]
    
    for template in templates:
        print(f"Template URI: {template.uriTemplate}")
        print(f"Name: {template.name}")
        print(f"Description: {template.description}")
        # Access tags and other metadata
        if hasattr(template, '_meta') and template._meta:
            fastmcp_meta = template._meta.get('_fastmcp', {})
            print(f"Tags: {fastmcp_meta.get('tags', [])}")
```

### Filtering by Tags

<VersionBadge version="2.11.0" />

You can use the `meta` field to filter resources based on their tags:

```python
async with client:
    resources = await client.list_resources()
    
    # Filter resources by tag
    config_resources = [
        resource for resource in resources 
        if hasattr(resource, '_meta') and resource._meta and
           resource._meta.get('_fastmcp', {}) and
           'config' in resource._meta.get('_fastmcp', {}).get('tags', [])
    ]
    
    print(f"Found {len(config_resources)} config resources")
```

<Note>
The `_meta` field is part of the standard MCP specification. FastMCP servers include tags and other metadata within a `_fastmcp` namespace (e.g., `_meta._fastmcp.tags`) to avoid conflicts with user-defined metadata. This behavior can be controlled with the server's `include_fastmcp_meta` setting - when disabled, the `_fastmcp` namespace won't be included. Other MCP server implementations may not provide this metadata structure.
</Note>

## Reading Resources

### Static Resources

Read a static resource using its URI:

```python
async with client:
    # Read a static resource
    content = await client.read_resource("file:///path/to/README.md")
    # content -> list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents]
    
    # Access text content
    if hasattr(content[0], 'text'):
        print(content[0].text)
    
    # Access binary content
    if hasattr(content[0], 'blob'):
        print(f"Binary data: {len(content[0].blob)} bytes")
```

### Resource Templates

Read from a resource template by providing the URI with parameters:

```python
async with client:
    # Read a resource generated from a template
    # For example, a template like "weather://{{city}}/current"
    weather_content = await client.read_resource("weather://london/current")
    
    # Access the generated content
    print(weather_content[0].text)  # Assuming text JSON response
```

## Content Types

Resources can return different content types:

### Text Resources

```python
async with client:
    content = await client.read_resource("resource://config/settings.json")
    
    for item in content:
        if hasattr(item, 'text'):
            print(f"Text content: {item.text}")
            print(f"MIME type: {item.mimeType}")
```

### Binary Resources

```python
async with client:
    content = await client.read_resource("resource://images/logo.png")
    
    for item in content:
        if hasattr(item, 'blob'):
            print(f"Binary content: {len(item.blob)} bytes")
            print(f"MIME type: {item.mimeType}")
            
            # Save to file
            with open("downloaded_logo.png", "wb") as f:
                f.write(item.blob)
```

## Working with Multi-Server Clients

When using multi-server clients, resource URIs are automatically prefixed with the server name:

```python
async with client:  # Multi-server client
    # Access resources from different servers
    weather_icons = await client.read_resource("weather://weather/icons/sunny")
    templates = await client.read_resource("resource://assistant/templates/list")
    
    print(f"Weather icon: {weather_icons[0].blob}")
    print(f"Templates: {templates[0].text}")
```

## Raw MCP Protocol Access

For access to the complete MCP protocol objects, use the `*_mcp` methods:

```python
async with client:
    # Raw MCP methods return full protocol objects
    resources_result = await client.list_resources_mcp()
    # resources_result -> mcp.types.ListResourcesResult
    
    templates_result = await client.list_resource_templates_mcp()
    # templates_result -> mcp.types.ListResourceTemplatesResult
    
    content_result = await client.read_resource_mcp("resource://example")
    # content_result -> mcp.types.ReadResourceResult
```

## Common Resource URI Patterns

Different MCP servers may use various URI schemes:

```python
# File system resources
"file:///path/to/file.txt"

# Custom protocol resources  
"weather://london/current"
"database://users/123"

# Generic resource protocol
"resource://config/settings"
"resource://templates/email"
```

<Tip>
Resource URIs and their formats depend on the specific MCP server implementation. Check the server's documentation for available resources and their URI patterns.
</Tip>