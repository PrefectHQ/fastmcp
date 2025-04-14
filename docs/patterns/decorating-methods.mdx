---
title: Decorating Methods
sidebarTitle: Decorating Methods
description: Properly use instance methods, class methods, and static methods with FastMCP decorators.
icon: at
---

FastMCP's decorator system is designed to work with functions, but you may see unexpected behavior if you try to decorate an instance or class method. This guide explains the correct approach for using methods with all FastMCP decorators (`@tool()`, `@resource()`, and `@prompt()`).

## Why Are Methods Hard?

When you apply a FastMCP decorator like `@tool()`, `@resource()`, or `@prompt()` to a method, the decorator captures the function at decoration time. For instance methods and class methods, this poses a challenge because:

1. For instance methods: The decorator gets the unbound method before any instance exists
2. For class methods: The decorator gets the function before it's bound to the class

This means directly decorating these methods doesn't work as expected. In practice, the LLM would see parameters like `self` or `cls` that it cannot provide values for.

## Recommended Patterns

### Instance Methods

**Don't do this** (it doesn't work properly):

```python
from fastmcp import FastMCP

mcp = FastMCP()

class MyClass:
    @mcp.tool()  # This won't work correctly
    def add(self, x, y):
        return x + y
    
    @mcp.resource("resource://{param}")  # This won't work correctly
    def get_resource(self, param: str):
        return f"Resource data for {param}"
```

When the decorator is applied this way, it captures the unbound method. When the LLM later tries to use this component, it will see `self` as a required parameter, but it won't know what to provide for it, causing errors or unexpected behavior.

**Do this instead**:

```python
from fastmcp import FastMCP

mcp = FastMCP()

class MyClass:
    def add(self, x, y):
        return x + y
    
    def get_resource(self, param: str):
        return f"Resource data for {param}"

# Create an instance first, then add the bound methods
obj = MyClass()
mcp.add_tool(obj.add)
mcp.add_resource_fn(obj.get_resource, uri="resource://{param}")  # For resources or templates

# Note: FastMCP provides add_resource() for adding Resource objects directly and
# add_resource_fn() for adding functions that generate resources or templates

# Now you can call it without 'self' showing up as a parameter
await mcp.call_tool('add', {'x': 1, 'y': 2})  # Returns 3
```

This approach works because:
1. You first create an instance of the class (`obj`)
2. When you access the method through the instance (`obj.add`), Python creates a bound method where `self` is already set to that instance
3. When you register this bound method, the system sees a callable that only expects the appropriate parameters, not `self`

### Class Methods

Similar to instance methods, decorating class methods directly doesn't work properly:

**Don't do this**:

```python
from fastmcp import FastMCP

mcp = FastMCP()

class MyClass:
    @classmethod
    @mcp.tool()  # This won't work correctly
    def from_string(cls, s):
        return cls(s)
```

The problem here is that the FastMCP decorator is applied before the `@classmethod` decorator (Python applies decorators bottom-to-top). So it captures the function before it's transformed into a class method, leading to incorrect behavior.

**Do this instead**:

```python
from fastmcp import FastMCP

mcp = FastMCP()

class MyClass:
    @classmethod
    def from_string(cls, s):
        return cls(s)

# Add the class method after the class is defined
mcp.add_tool(MyClass.from_string)
```

This works because:
1. The `@classmethod` decorator is applied properly during class definition
2. When you access `MyClass.from_string`, Python provides a special method object that automatically binds the class to the `cls` parameter
3. When registered, only the appropriate parameters are exposed to the LLM, hiding the implementation detail of the `cls` parameter

### Static Methods

Unlike instance and class methods, static methods work fine with FastMCP decorators:

```python
from fastmcp import FastMCP

mcp = FastMCP()

class MyClass:
    @staticmethod
    @mcp.tool()  # This works!
    def utility(x, y):
        return x + y
    
    @staticmethod
    @mcp.resource("resource://data")  # This works too!
    def get_data():
        return "Static resource data"
```

This approach works because:
1. The `@staticmethod` decorator is applied first (executed last), transforming the method into a regular function
2. When the FastMCP decorator is applied, it's capturing what is effectively just a regular function
3. A static method doesn't have any binding requirements - it doesn't receive a `self` or `cls` parameter

Alternatively, you can use the same pattern as the other methods:

```python
from fastmcp import FastMCP

mcp = FastMCP()

class MyClass:
    @staticmethod
    def utility(x, y):
        return x + y

# This also works
mcp.add_tool(MyClass.utility)
```

This works for the same reason - a static method is essentially just a function in a class namespace.

## Additional Patterns

### Creating Components at Class Initialization

You can automatically register instance methods when creating an object:

```python
from fastmcp import FastMCP

mcp = FastMCP()

class ComponentProvider:
    def __init__(self, mcp_instance):
        # Register methods
        mcp_instance.add_tool(self.tool_method)
        mcp_instance.add_resource_fn(self.resource_method, uri="resource://data")
    
    def tool_method(self, x):
        return x * 2
    
    def resource_method(self):
        return "Resource data"

# The methods are automatically registered when creating the instance
provider = ComponentProvider(mcp)
```

This pattern is useful when:
- You want to encapsulate registration logic within the class itself
- You have multiple related components that should be registered together
- You want to ensure that methods are always properly registered when creating an instance

The class automatically registers its methods during initialization, ensuring they're properly bound to the instance before registration.

## Summary

While FastMCP's decorator pattern works seamlessly with regular functions and static methods, for instance methods and class methods, you should add them after creating the instance or class. This ensures that the methods are properly bound before being registered.

These patterns apply to all FastMCP decorators and registration methods:
- `@tool()` and `add_tool()`
- `@resource()` and `add_resource_fn()`
- `@prompt()` and `add_prompt()`

Understanding these patterns allows you to effectively organize your components into classes while maintaining proper method binding, giving you the benefits of object-oriented design without sacrificing the simplicity of FastMCP's decorator system.
