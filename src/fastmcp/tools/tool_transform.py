"""# Tool Transformation

Transform existing tools with modified schemas, argument mappings, and custom behavior.
Use this for creating tool variants, adapting tools for different contexts, or adding
custom logic while preserving the original tool's functionality.

## Quick Reference

### Basic Argument Renaming
```python
# Transform specific parent arguments (others pass through unchanged)
new_tool = Tool.from_tool(
    original_tool,
    transform_args={"old_param": "new_param"}  # Only transforms this one arg
)
```

### Complex Transformations
```python
from fastmcp.tools.tool_transform import ArgTransform

new_tool = Tool.from_tool(
    original_tool,
    transform_args={
        "old_name": ArgTransform(name="new_name", description="Updated desc"),
        "hidden_param": ArgTransform(hide=True, default="constant_value"),
        "simple": "renamed"
    }
)
```

### Custom Transform Functions
```python
async def my_transform(new_x: int, new_y: int) -> str:
    # Use forward() with transformed argument names
    result = await forward(new_x=new_x, new_y=new_y)
    return f"Custom: {result}"

new_tool = Tool.from_tool(
    original_tool,
    transform_fn=my_transform,
    transform_args={"x": "new_x", "y": "new_y"}
)
```

### Using **kwargs for Flexibility
```python
async def flexible_transform(**kwargs) -> str:
    # kwargs contains all transformed arguments
    result = await forward(**kwargs)
    return f"Got: {kwargs}"

new_tool = Tool.from_tool(
    original_tool,
    transform_fn=flexible_transform,
    transform_args={"x": "input_x", "y": "input_y"}
)
```

## Key Functions

- `forward(**kwargs)`: Call parent tool with transformed argument names
- `forward_raw(**kwargs)`: Call parent tool with original argument names

## Important Notes

- `transform_args` is optional - if empty/None, all parent arguments pass through unchanged
- Only arguments listed in `transform_args` are transformed, others remain as-is
- Functions with `**kwargs` receive both transformed and untransformed arguments

## ArgTransform Options

- `name`: Rename the argument
- `description`: Change the description
- `default`: Add/change default value
- `hide=True`: Hide the argument from clients (pass constant value to parent)

## Common Patterns

```python
# Chain transformations (partial transforms at each step)
tool1 = Tool.from_tool(original, transform_args={"a": "x"})  # Only transforms 'a'
tool2 = Tool.from_tool(tool1, transform_args={"x": "final"})  # Only transforms 'x'

# Pure passthrough (no transform_args needed)
enhanced = Tool.from_tool(
    original,
    name="enhanced_version",
    description="Better tool",
    tags={"v2", "enhanced"}
    # No transform_args = all parent args pass through unchanged
)

# Hide specific arguments with constant values
simplified = Tool.from_tool(
    complex_tool,
    transform_args={
        "api_key": ArgTransform(hide=True, default="secret_key"),  # Hidden constant
        "debug": ArgTransform(hide=True)  # Hidden, uses parent's default
    }
)
```
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from types import EllipsisType
from typing import TYPE_CHECKING, Any

from mcp.types import EmbeddedResource, ImageContent, TextContent, ToolAnnotations

from fastmcp.tools.tool import ParsedFunction, Tool
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import get_cached_typeadapter

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


# Context variable to store current transformed tool
_current_tool: ContextVar[TransformedTool | None] = ContextVar(
    "_current_tool", default=None
)


async def forward(**kwargs) -> Any:
    """Forward to parent tool with argument transformation applied.

    This function can only be called from within a transformed tool's custom
    function. It applies argument transformation (renaming, validation) before
    calling the parent tool.

    For example, if the parent tool has args `x` and `y`, but the transformed
    tool has args `a` and `b`, and an `transform_args` was provided that maps `x` to
    `a` and `y` to `b`, then `forward(a=1, b=2)` will call the parent tool with
    `x=1` and `y=2`.

    Args:
        **kwargs: Arguments to forward to the parent tool (using transformed names).

    Returns:
        The result from the parent tool execution.

    Raises:
        RuntimeError: If called outside a transformed tool context.
        TypeError: If provided arguments don't match the transformed schema.
    """
    tool = _current_tool.get()
    if tool is None:
        raise RuntimeError("forward() can only be called within a transformed tool")

    # Use the forwarding function that handles mapping
    return await tool.forwarding_fn(**kwargs)


async def forward_raw(**kwargs) -> Any:
    """Forward directly to parent tool without transformation.

    This function bypasses all argument transformation and validation, calling the parent
    tool directly with the provided arguments. Use this when you need to call the parent
    with its original parameter names and structure.

    For example, if the parent tool has args `x` and `y`, then `forward_raw(x=1,
    y=2)` will call the parent tool with `x=1` and `y=2`.

    Args:
        **kwargs: Arguments to pass directly to the parent tool (using original names).

    Returns:
        The result from the parent tool execution.

    Raises:
        RuntimeError: If called outside a transformed tool context.
    """
    tool = _current_tool.get()
    if tool is None:
        raise RuntimeError("forward_raw() can only be called within a transformed tool")

    return await tool.parent_tool.run(kwargs)


@dataclass(kw_only=True)
class ArgTransform:
    """Configuration for transforming a parent tool's argument.

    This class allows fine-grained control over how individual arguments are transformed
    when creating a new tool from an existing one. You can rename arguments, change their
    descriptions, add default values, or hide them from clients while passing constants.

    Attributes:
        name: New name for the argument. Use None to keep original name, or ... for no change.
        description: New description for the argument. Use None to remove description, or ... for no change.
        default: New default value for the argument. Use ... for no change.
        type: New type for the argument. Use ... for no change.
        hide: If True, hide this argument from clients but pass a constant value to parent.

    Examples:
        # Rename argument 'old_name' to 'new_name'
        ArgTransform(name="new_name")

        # Change description only
        ArgTransform(description="Updated description")

        # Add a default value (makes argument optional)
        ArgTransform(default=42)

        # Change the type
        ArgTransform(type=str)

        # Hide the argument entirely from clients
        ArgTransform(hide=True)

        # Hide argument but pass a constant value to parent
        ArgTransform(hide=True, default="constant_value")

        # Combine multiple transformations
        ArgTransform(name="new_name", description="New desc", default=None, type=int)
    """

    name: str | None | EllipsisType = ...
    description: str | None | EllipsisType = ...
    default: Any | EllipsisType = ...
    type: Any | EllipsisType = ...
    hide: bool = False


class TransformedTool(Tool):
    """A tool that is transformed from another tool.

    This class represents a tool that has been created by transforming another tool.
    It supports argument renaming, schema modification, custom function injection,
    and provides context for the forward() and forward_raw() functions.

    The transformation can be purely schema-based (argument renaming, dropping, etc.)
    or can include a custom function that uses forward() to call the parent tool
    with transformed arguments.

    Attributes:
        parent_tool: The original tool that this tool was transformed from.
        fn: The function to execute when this tool is called (either the forwarding
            function for pure transformations or a custom user function).
        forwarding_fn: Internal function that handles argument transformation and
            validation when forward() is called from custom functions.
    """

    parent_tool: Tool
    fn: Callable[..., Any]
    forwarding_fn: Callable[..., Any]  # Always present, handles arg transformation

    async def run(
        self, arguments: dict[str, Any]
    ) -> list[TextContent | ImageContent | EmbeddedResource]:
        """Run the tool with context set for forward() functions.

        This method executes the tool's function while setting up the context
        that allows forward() and forward_raw() to work correctly within custom
        functions.

        Args:
            arguments: Dictionary of arguments to pass to the tool's function.

        Returns:
            List of content objects (text, image, or embedded resources) representing
            the tool's output.
        """
        from fastmcp.tools.tool import _convert_to_content

        # Fill in missing arguments with schema defaults to ensure
        # ArgTransform defaults take precedence over function defaults
        arguments = arguments.copy()
        properties = self.parameters.get("properties", {})

        for param_name, param_schema in properties.items():
            if param_name not in arguments and "default" in param_schema:
                arguments[param_name] = param_schema["default"]

        token = _current_tool.set(self)
        try:
            result = await self.fn(**arguments)
            return _convert_to_content(result, serializer=self.serializer)
        finally:
            _current_tool.reset(token)

    @classmethod
    def from_tool(
        cls,
        tool: Tool,
        transform_fn: Callable[..., Any] | None = None,
        name: str | None = None,
        transform_args: dict[str, str | ArgTransform | None] | None = None,
        description: str | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
        serializer: Callable[[Any], str] | None = None,
    ) -> TransformedTool:
        """Create a transformed tool from a parent tool.

        Args:
            tool: The parent tool to transform.
            transform_fn: Optional custom function. Can use forward() and forward_raw()
                to call the parent tool. Functions with **kwargs receive transformed
                argument names.
            name: New name for the tool. Defaults to parent tool's name.
            transform_args: Optional transformations for parent tool arguments.
                Only specified arguments are transformed, others pass through unchanged:
                - str: Simple rename
                - ArgTransform: Complex transformation (rename/description/default/drop)
                - None: Drop the argument
            description: New description. Defaults to parent's description.
            tags: New tags. Defaults to parent's tags.
            annotations: New annotations. Defaults to parent's annotations.
            serializer: New serializer. Defaults to parent's serializer.

        Returns:
            TransformedTool with the specified transformations.

                Examples:
            # Transform specific arguments only
            Tool.from_tool(parent, transform_args={"old": "new"})  # Others unchanged

            # Custom function with partial transforms
            async def custom(x: int, y: int) -> str:
                result = await forward(x=x, y=y)
                return f"Custom: {result}"

            Tool.from_tool(parent, transform_fn=custom, transform_args={"a": "x", "b": "y"})

            # Using **kwargs (gets all args, transformed and untransformed)
            async def flexible(**kwargs) -> str:
                result = await forward(**kwargs)
                return f"Got: {kwargs}"

            Tool.from_tool(parent, transform_fn=flexible, transform_args={"a": "x"})
        """

        # Validate transform_args early
        if transform_args:
            parent_params = set(tool.parameters.get("properties", {}).keys())
            unknown_args = set(transform_args.keys()) - parent_params
            if unknown_args:
                raise ValueError(
                    f"Unknown arguments in transform_args: {', '.join(sorted(unknown_args))}. "
                    f"Parent tool has: {', '.join(sorted(parent_params))}"
                )

        # Always create the forwarding transform
        schema, forwarding_fn = cls._create_forwarding_transform(tool, transform_args)

        if transform_fn is None:
            # User wants pure transformation - use forwarding_fn as the main function
            final_fn = forwarding_fn
            final_schema = schema
        else:
            # User provided custom function - merge schemas
            parsed_fn = ParsedFunction.from_function(transform_fn, validate=False)
            final_fn = transform_fn

            has_kwargs = cls._function_has_kwargs(transform_fn)

            # Validate function parameters against transformed schema
            fn_params = set(parsed_fn.parameters.get("properties", {}).keys())
            transformed_params = set(schema.get("properties", {}).keys())

            if not has_kwargs:
                # Without **kwargs, function must declare all transformed params
                # Check if function is missing any parameters required after transformation
                missing_params = transformed_params - fn_params
                if missing_params:
                    raise ValueError(
                        f"Function missing parameters required after transformation: "
                        f"{', '.join(sorted(missing_params))}. "
                        f"Function declares: {', '.join(sorted(fn_params))}"
                    )

                # ArgTransform takes precedence over function signature
                # Start with function schema as base, then override with transformed schema
                final_schema = cls._merge_schema_with_precedence(
                    parsed_fn.parameters, schema
                )
            else:
                # With **kwargs, function can access all transformed params
                # ArgTransform takes precedence over function signature
                # No validation needed - kwargs makes everything accessible

                # Start with function schema as base, then override with transformed schema
                final_schema = cls._merge_schema_with_precedence(
                    parsed_fn.parameters, schema
                )

        # Additional validation: check for naming conflicts after transformation
        if transform_args:
            new_names = []
            for old_name, transform in transform_args.items():
                if isinstance(transform, str):
                    new_names.append(transform)
                elif isinstance(transform, ArgTransform) and not transform.hide:
                    if transform.name is not ... and transform.name is not None:
                        new_names.append(transform.name)
                    else:
                        new_names.append(old_name)

            # Check for duplicate names after transformation
            name_counts = {}
            for arg_name in new_names:
                name_counts[arg_name] = name_counts.get(arg_name, 0) + 1

            duplicates = [
                arg_name for arg_name, count in name_counts.items() if count > 1
            ]
            if duplicates:
                raise ValueError(
                    f"Multiple arguments would be mapped to the same names: "
                    f"{', '.join(sorted(duplicates))}"
                )

        final_description = description if description is not None else tool.description

        return cls(
            fn=final_fn,
            forwarding_fn=forwarding_fn,
            parent_tool=tool,
            name=name or tool.name,
            description=final_description,
            parameters=final_schema,
            tags=tags or tool.tags,
            annotations=annotations or tool.annotations,
            serializer=serializer or tool.serializer,
        )

    @classmethod
    def _create_forwarding_transform(
        cls,
        parent_tool: Tool,
        transform_args: dict[str, str | ArgTransform | None] | None,
    ) -> tuple[dict[str, Any], Callable[..., Any]]:
        """Create schema and forwarding function that encapsulates all transformation logic.

        This method builds a new JSON schema for the transformed tool and creates a
        forwarding function that validates arguments against the new schema and maps
        them back to the parent tool's expected arguments.

        Args:
            parent_tool: The original tool to transform.
            transform_args: Dictionary defining how to transform each argument.

        Returns:
            A tuple containing:
            - dict: The new JSON schema for the transformed tool
            - Callable: Async function that validates and forwards calls to the parent tool
        """

        # Build transformed schema and mapping
        parent_props = parent_tool.parameters.get("properties", {}).copy()
        parent_required = set(parent_tool.parameters.get("required", []))

        new_props = {}
        new_required = set()
        new_to_old = {}
        hidden_defaults = {}  # Track hidden parameters with constant values

        for old_name, old_schema in parent_props.items():
            # Check if parameter is in transform_args
            if transform_args and old_name in transform_args:
                transform = transform_args[old_name]
            else:
                transform = ...  # Default behavior - pass through

            # Handle hidden parameters with defaults
            if isinstance(transform, ArgTransform) and transform.hide:
                # Validate that hidden parameters without user defaults have parent defaults
                if transform.default is ... and old_name in parent_required:
                    raise ValueError(
                        f"Hidden parameter '{old_name}' has no default value in parent tool "
                        f"and no default provided in ArgTransform. Either provide a default "
                        f"in ArgTransform or don't hide required parameters."
                    )
                if transform.default is not ...:
                    # Hidden parameter with a constant value
                    hidden_defaults[old_name] = transform.default
                # Skip adding to schema (not exposed to clients)
                continue

            transform_result = cls._apply_single_transform(
                old_name,
                old_schema,
                transform,
                old_name in parent_required,
            )

            if transform_result:
                new_name, new_schema, is_required = transform_result
                new_props[new_name] = new_schema
                new_to_old[new_name] = old_name
                if is_required:
                    new_required.add(new_name)

        schema = {
            "type": "object",
            "properties": new_props,
            "required": list(new_required),
        }

        # Create forwarding function that closes over everything it needs
        async def _forward(**kwargs):
            # Validate arguments
            valid_args = set(new_props.keys())
            provided_args = set(kwargs.keys())
            unknown_args = provided_args - valid_args

            if unknown_args:
                raise TypeError(
                    f"Got unexpected keyword argument(s): {', '.join(sorted(unknown_args))}"
                )

            # Check required arguments
            missing_args = new_required - provided_args
            if missing_args:
                raise TypeError(
                    f"Missing required argument(s): {', '.join(sorted(missing_args))}"
                )

            # Map arguments to parent names
            parent_args = {}
            for new_name, value in kwargs.items():
                old_name = new_to_old.get(new_name, new_name)
                parent_args[old_name] = value

            # Add hidden defaults (constant values for hidden parameters)
            parent_args.update(hidden_defaults)

            return await parent_tool.run(parent_args)

        return schema, _forward

    @staticmethod
    def _apply_single_transform(
        old_name: str,
        old_schema: dict[str, Any],
        transform: str | ArgTransform | None | EllipsisType,
        is_required: bool,
    ) -> tuple[str, dict[str, Any], bool] | None:
        """Apply transformation to a single parameter.

        This method handles the transformation of a single argument according to
        the specified transformation rules.

        Args:
            old_name: Original name of the parameter.
            old_schema: Original JSON schema for the parameter.
            transform: Transformation to apply (string for rename, ArgTransform for complex,
                      None to drop, ... to pass through unchanged).
            is_required: Whether the original parameter was required.

        Returns:
            Tuple of (new_name, new_schema, new_is_required) if parameter should be kept,
            None if parameter should be dropped.
        """
        if transform is ...:
            # Not in transform_args - pass through
            return old_name, old_schema.copy(), is_required
        elif transform is None:
            # Explicitly set to None in transform_args - drop the parameter
            return None

        if isinstance(transform, str):
            # Simple rename
            return transform, old_schema.copy(), is_required

        if isinstance(transform, ArgTransform):
            if transform.hide:
                return None

            if transform.name is not ...:
                new_name = transform.name or old_name  # Handle None case
            else:
                new_name = old_name
            new_schema = old_schema.copy()

            if transform.description is not ...:
                new_schema["description"] = transform.description
            if transform.default is not ...:
                new_schema["default"] = transform.default
                is_required = False
            if transform.type is not ...:
                # Use TypeAdapter to get proper JSON schema for the type
                type_schema = get_cached_typeadapter(transform.type).json_schema()
                # Update the schema with the type information from TypeAdapter
                new_schema.update(type_schema)

            return new_name, new_schema, is_required  # type: ignore[return-value]

        raise ValueError(f"Invalid transform: {transform}")

    @staticmethod
    def _merge_schema_with_precedence(
        base_schema: dict[str, Any], override_schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge two schemas, with the override schema taking precedence.

        Args:
            base_schema: Base schema to start with
            override_schema: Schema that takes precedence for overlapping properties

        Returns:
            Merged schema with override taking precedence
        """
        merged_props = base_schema.get("properties", {}).copy()
        merged_required = set(base_schema.get("required", []))

        override_props = override_schema.get("properties", {})
        override_required = set(override_schema.get("required", []))

        # Override properties
        for param_name, param_schema in override_props.items():
            if param_name in merged_props:
                # Merge the schemas, with override taking precedence
                base_param = merged_props[param_name].copy()
                base_param.update(param_schema)
                merged_props[param_name] = base_param
            else:
                merged_props[param_name] = param_schema.copy()

        # Handle required parameters - override takes complete precedence
        # Start with override's required set
        final_required = override_required.copy()

        # For parameters not in override, inherit base requirement status
        # but only if they don't have a default in the final merged properties
        for param_name in merged_required:
            if param_name not in override_props:
                # Parameter not mentioned in override, keep base requirement status
                final_required.add(param_name)
            elif (
                param_name in override_props
                and "default" not in merged_props[param_name]
            ):
                # Parameter in override but no default, keep required if it was required in base
                if param_name not in override_required:
                    # Override doesn't specify it as required, and it has no default,
                    # so inherit from base
                    final_required.add(param_name)

        # Remove any parameters that have defaults (they become optional)
        for param_name, param_schema in merged_props.items():
            if "default" in param_schema:
                final_required.discard(param_name)

        return {
            "type": "object",
            "properties": merged_props,
            "required": list(final_required),
        }

    @staticmethod
    def _function_has_kwargs(fn: Callable[..., Any]) -> bool:
        """Check if function accepts **kwargs.

        This determines whether a custom function can accept arbitrary keyword arguments,
        which affects how schemas are merged during tool transformation.

        Args:
            fn: Function to inspect.

        Returns:
            True if the function has a **kwargs parameter, False otherwise.
        """
        sig = inspect.signature(fn)
        return any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
