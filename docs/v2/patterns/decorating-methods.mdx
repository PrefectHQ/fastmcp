---
title: Decorating Methods
sidebarTitle: Decorating Methods
description: Properly use instance methods, class methods, and static methods with FastMCP decorators.
icon: at
---

FastMCP's decorator system is designed to work with functions, but you may see unexpected behavior if you try to decorate an instance or class method. This guide explains the correct approach for using methods with all FastMCP decorators (`@tool`, `@resource`, and `@prompt`).

## Why Are Methods Hard?

When you apply a FastMCP decorator like `@tool`, `@resource`, or `@prompt` to a method, the decorator captures the function at decoration time. For instance methods and class methods, this poses a challenge because:

1. For instance methods: The decorator gets the unbound method before any instance exists
2. For class methods: The decorator gets the function before it's bound to the class

This means directly decorating these methods doesn't work as expected. In practice, the LLM would see parameters like `self` or `cls` that it cannot provide values for.

Additionally, **FastMCP decorators return objects (Tool, Resource, or Prompt instances) rather than the original function**. This means that when you decorate a method directly, the method becomes the returned object and is no longer callable by your code:

<Warning>
**Don't do this!**

The method will no longer be callable from Python, and the tool won't be callable by LLMs.

```python

from fastmcp import FastMCP
mcp = FastMCP()

class MyClass:
    @mcp.tool
    def my_method(self, x: int) -> int:
        return x * 2

obj = MyClass()
obj.my_method(5)  # Fails - my_method is a Tool, not a function
```
</Warning>

This is another important reason to register methods functionally after defining the class.

## Recommended Patterns

### Instance Methods

<Warning>
**Don't do this!**

```python
from fastmcp import FastMCP

mcp = FastMCP()

class MyClass:
    @mcp.tool  # This won't work correctly
    def add(self, x, y):
        return x + y
```
</Warning>
When the decorator is applied this way, it captures the unbound method. When the LLM later tries to use this component, it will see `self` as a required parameter, but it won't know what to provide for it, causing errors or unexpected behavior.

<Check>
**Do this instead**:

```python
from fastmcp import FastMCP

mcp = FastMCP()

class MyClass:
    def add(self, x, y):
        return x + y

# Create an instance first, then register the bound methods
obj = MyClass()
mcp.tool(obj.add)

# Now you can call it without 'self' showing up as a parameter
await mcp._mcp_call_tool('add', {'x': 1, 'y': 2})  # Returns 3
```
</Check>

This approach works because:
1. You first create an instance of the class (`obj`)
2. When you access the method through the instance (`obj.add`), Python creates a bound method where `self` is already set to that instance
3. When you register this bound method, the system sees a callable that only expects the appropriate parameters, not `self`

### Class Methods

The behavior of decorating class methods depends on the order of decorators:

<Warning>
**Don't do this** (decorator order matters):

```python
from fastmcp import FastMCP

mcp = FastMCP()

class MyClass:
    @classmethod
    @mcp.tool  # This won't work but won't raise an error
    def from_string_v1(cls, s):
        return cls(s)
    
    @mcp.tool
    @classmethod  # This will raise a helpful ValueError
    def from_string_v2(cls, s):
        return cls(s)
```
</Warning>

- If `@classmethod` comes first, then `@mcp.tool`: No error is raised, but it won't work correctly
- If `@mcp.tool` comes first, then `@classmethod`: FastMCP will detect this and raise a helpful `ValueError` with guidance

<Check>
**Do this instead**:

```python
from fastmcp import FastMCP

mcp = FastMCP()

class MyClass:
    @classmethod
    def from_string(cls, s):
        return cls(s)

# Register the class method after the class is defined
mcp.tool(MyClass.from_string)
```
</Check>

This works because:
1. The `@classmethod` decorator is applied properly during class definition
2. When you access `MyClass.from_string`, Python provides a special method object that automatically binds the class to the `cls` parameter
3. When registered, only the appropriate parameters are exposed to the LLM, hiding the implementation detail of the `cls` parameter

### Static Methods

Static methods "work" with FastMCP decorators, but this is not recommended because the FastMCP decorator will not return a callable method. Therefore, you should register static methods the same way as other methods.

<Warning>
**This is not recommended, though it will work.**

```python
from fastmcp import FastMCP

mcp = FastMCP()

class MyClass:
    @mcp.tool
    @staticmethod
    def utility(x, y):
        return x + y
```
</Warning>

This works because `@staticmethod` converts the method to a regular function, which the FastMCP decorator can then properly process. However, this is not recommended because the FastMCP decorator will not return a callable staticmethod. Therefore, you should register static methods the same way as other methods.

<Check>
**Prefer this pattern:**

```python
from fastmcp import FastMCP

mcp = FastMCP()

class MyClass:
    @staticmethod
    def utility(x, y):
        return x + y

# This also works
mcp.tool(MyClass.utility)
```
</Check>

## Additional Patterns

### Creating Components at Class Initialization

You can automatically register instance methods when creating an object:

```python
from fastmcp import FastMCP

mcp = FastMCP()

class ComponentProvider:
    def __init__(self, mcp_instance):
        # Register methods
        mcp_instance.tool(self.tool_method)
        mcp_instance.resource("resource://data")(self.resource_method)
    
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

The current behavior of FastMCP decorators with methods is:

- **Static methods**: Can be decorated directly and work perfectly with all FastMCP decorators
- **Class methods**: Cannot be decorated directly and will raise a helpful `ValueError` with guidance
- **Instance methods**: Should be registered after creating an instance using the decorator calls

For class and instance methods, you should register them after creating the instance or class to ensure proper method binding. This ensures that the methods are properly bound before being registered.


Understanding these patterns allows you to effectively organize your components into classes while maintaining proper method binding, giving you the benefits of object-oriented design without sacrificing the simplicity of FastMCP's decorator system.
