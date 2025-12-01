from __future__ import annotations

import inspect
from collections.abc import Callable
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast

import pydantic_core
from mcp.types import ToolAnnotations
from pydantic import ConfigDict
from pydantic.fields import Field
from pydantic.functional_validators import BeforeValidator

from fastmcp.tools.tool import ParsedFunction, Tool, ToolResult, _convert_to_content
from fastmcp.utilities.components import _convert_set_default_none
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    FastMCPBaseModel,
    NotSet,
    NotSetT,
    get_cached_typeadapter,
)

logger = get_logger(__name__)


# Context variable to store current transformed tool
_current_tool: ContextVar[TransformedTool | None] = ContextVar(  # type: ignore[assignment]
    "_current_tool", default=None
)


async def forward(**kwargs: Any) -> ToolResult:
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
        The ToolResult from the parent tool execution.

    Raises:
        RuntimeError: If called outside a transformed tool context.
        TypeError: If provided arguments don't match the transformed schema.
    """
    tool = _current_tool.get()
    if tool is None:
        raise RuntimeError("forward() can only be called within a transformed tool")

    # Use the forwarding function that handles mapping
    return await tool.forwarding_fn(**kwargs)


async def forward_raw(**kwargs: Any) -> ToolResult:
    """Forward directly to parent tool without transformation.

    This function bypasses all argument transformation and validation, calling the parent
    tool directly with the provided arguments. Use this when you need to call the parent
    with its original parameter names and structure.

    For example, if the parent tool has args `x` and `y`, then `forward_raw(x=1,
    y=2)` will call the parent tool with `x=1` and `y=2`.

    Args:
        **kwargs: Arguments to pass directly to the parent tool (using original names).

    Returns:
        The ToolResult from the parent tool execution.

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
        default_factory: Callable that returns a default value. Cannot be used with default.
        type: New type for the argument. Use ... for no change.
        hide: If True, hide this argument from clients but pass a constant value to parent.
        required: If True, make argument required (remove default). Use ... for no change.
        examples: Examples for the argument. Use ... for no change.

    Examples:
        Rename argument 'old_name' to 'new_name'
        ```python
        ArgTransform(name="new_name")
        ```

        Change description only
        ```python
        ArgTransform(description="Updated description")
        ```

        Add a default value (makes argument optional)
        ```python
        ArgTransform(default=42)
        ```

        Add a default factory (makes argument optional)
        ```python
        ArgTransform(default_factory=lambda: time.time())
        ```

        Change the type
        ```python
        ArgTransform(type=str)
        ```

        Hide the argument entirely from clients
        ```python
        ArgTransform(hide=True)
        ```

        Hide argument but pass a constant value to parent
        ```python
        ArgTransform(hide=True, default="constant_value")
        ```

        Hide argument but pass a factory-generated value to parent
        ```python
        ArgTransform(hide=True, default_factory=lambda: uuid.uuid4().hex)
        ```

        Make an optional parameter required (removes any default)
        ```python
        ArgTransform(required=True)
        ```

        Combine multiple transformations
        ```python
        ArgTransform(name="new_name", description="New desc", default=None, type=int)
        ```
    """

    name: str | NotSetT = NotSet
    description: str | NotSetT = NotSet
    default: Any | NotSetT = NotSet
    default_factory: Callable[[], Any] | NotSetT = NotSet
    type: Any | NotSetT = NotSet
    hide: bool = False
    required: Literal[True] | NotSetT = NotSet
    examples: Any | NotSetT = NotSet

    def __post_init__(self):
        """Validate that only one of default or default_factory is provided."""
        has_default = self.default is not NotSet
        has_factory = self.default_factory is not NotSet

        if has_default and has_factory:
            raise ValueError(
                "Cannot specify both 'default' and 'default_factory' in ArgTransform. "
                "Use either 'default' for a static value or 'default_factory' for a callable."
            )

        if has_factory and not self.hide:
            raise ValueError(
                "default_factory can only be used with hide=True. "
                "Visible parameters must use static 'default' values since JSON schema "
                "cannot represent dynamic factories."
            )

        if self.required is True and (has_default or has_factory):
            raise ValueError(
                "Cannot specify 'required=True' with 'default' or 'default_factory'. "
                "Required parameters cannot have defaults."
            )

        if self.hide and self.required is True:
            raise ValueError(
                "Cannot specify both 'hide=True' and 'required=True'. "
                "Hidden parameters cannot be required since clients cannot provide them."
            )

        if self.required is False:
            raise ValueError(
                "Cannot specify 'required=False'. Set a default value instead."
            )


class ArgTransformConfig(FastMCPBaseModel):
    """A model for requesting a single argument transform."""

    name: str | None = Field(default=None, description="The new name for the argument.")
    description: str | None = Field(
        default=None, description="The new description for the argument."
    )
    default: str | int | float | bool | None = Field(
        default=None, description="The new default value for the argument."
    )
    hide: bool = Field(
        default=False, description="Whether to hide the argument from the tool."
    )
    required: Literal[True] | None = Field(
        default=None, description="Whether the argument is required."
    )
    examples: Any | None = Field(default=None, description="Examples of the argument.")

    def to_arg_transform(self) -> ArgTransform:
        """Convert the argument transform to a FastMCP argument transform."""

        return ArgTransform(**self.model_dump(exclude_unset=True))  # pyright: ignore[reportAny]


class TransformedTool(Tool):
    """A tool that is transformed from another tool.

    This class represents a tool that has been created by transforming another tool.
    It supports argument renaming, schema modification, custom function injection,
    structured output control, and provides context for the forward() and forward_raw() functions.

    The transformation can be purely schema-based (argument renaming, dropping, etc.)
    or can include a custom function that uses forward() to call the parent tool
    with transformed arguments. Output schemas and structured outputs are automatically
    inherited from the parent tool but can be overridden or disabled.

    Attributes:
        parent_tool: The original tool that this tool was transformed from.
        fn: The function to execute when this tool is called (either the forwarding
            function for pure transformations or a custom user function).
        forwarding_fn: Internal function that handles argument transformation and
            validation when forward() is called from custom functions.
    """

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    parent_tool: Tool
    fn: Callable[..., Any]
    forwarding_fn: Callable[..., Any]  # Always present, handles arg transformation
    transform_args: dict[str, ArgTransform]

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Run the tool with context set for forward() functions.

        This method executes the tool's function while setting up the context
        that allows forward() and forward_raw() to work correctly within custom
        functions.

        Args:
            arguments: Dictionary of arguments to pass to the tool's function.

        Returns:
            ToolResult object containing content and optional structured output.
        """

        # Fill in missing arguments with schema defaults to ensure
        # ArgTransform defaults take precedence over function defaults
        arguments = arguments.copy()
        properties = self.parameters.get("properties", {})

        for param_name, param_schema in properties.items():
            if param_name not in arguments and "default" in param_schema:
                # Check if this parameter has a default_factory from transform_args
                # We need to call the factory for each run, not use the cached schema value
                has_factory_default = False
                if self.transform_args:
                    # Find the original parameter name that maps to this param_name
                    for orig_name, transform in self.transform_args.items():
                        transform_name = (
                            transform.name
                            if transform.name is not NotSet
                            else orig_name
                        )
                        if (
                            transform_name == param_name
                            and transform.default_factory is not NotSet
                        ):
                            # Type check to ensure default_factory is callable
                            if callable(transform.default_factory):
                                arguments[param_name] = transform.default_factory()
                                has_factory_default = True
                                break

                if not has_factory_default:
                    arguments[param_name] = param_schema["default"]

        token = _current_tool.set(self)
        try:
            result = await self.fn(**arguments)

            # If transform function returns ToolResult, respect our output_schema setting
            if isinstance(result, ToolResult):
                if self.output_schema is None:
                    # Check if this is from a custom function that returns ToolResult

                    return_annotation = inspect.signature(self.fn).return_annotation
                    if return_annotation is ToolResult:
                        # Custom function returns ToolResult - preserve its content
                        return result
                    else:
                        # Forwarded call with no explicit schema - preserve parent's structured content
                        # The parent tool may have generated structured content via its own fallback logic
                        return result
                elif self.output_schema.get(
                    "type"
                ) != "object" and not self.output_schema.get("x-fastmcp-wrap-result"):
                    # Non-object explicit schemas disable structured content
                    return ToolResult(
                        content=result.content,
                        structured_content=None,
                    )
                else:
                    return result

            # Otherwise convert to content and create ToolResult with proper structured content

            unstructured_result = _convert_to_content(
                result, serializer=self.serializer
            )

            structured_output = None
            # First handle structured content based on output schema, if any
            if self.output_schema is not None:
                if self.output_schema.get("x-fastmcp-wrap-result"):
                    # Schema says wrap - always wrap in result key
                    structured_output = {"result": result}
                else:
                    structured_output = result
            # If no output schema, try to serialize the result. If it is a dict, use
            # it as structured content. If it is not a dict, ignore it.
            if structured_output is None:
                try:
                    structured_output = pydantic_core.to_jsonable_python(result)
                    if not isinstance(structured_output, dict):
                        structured_output = None
                except Exception:
                    pass

            return ToolResult(
                content=unstructured_result,
                structured_content=structured_output,
            )
        finally:
            _current_tool.reset(token)

    @classmethod
    def from_tool(
        cls,
        tool: Tool,
        name: str | None = None,
        title: str | NotSetT | None = NotSet,
        description: str | NotSetT | None = NotSet,
        tags: set[str] | None = None,
        transform_fn: Callable[..., Any] | None = None,
        transform_args: dict[str, ArgTransform] | None = None,
        annotations: ToolAnnotations | NotSetT | None = NotSet,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        serializer: Callable[[Any], str] | NotSetT | None = NotSet,
        meta: dict[str, Any] | NotSetT | None = NotSet,
        enabled: bool | None = None,
    ) -> TransformedTool:
        """Create a transformed tool from a parent tool.

        Args:
            tool: The parent tool to transform.
            transform_fn: Optional custom function. Can use forward() and forward_raw()
                to call the parent tool. Functions with **kwargs receive transformed
                argument names.
            name: New name for the tool. Defaults to parent tool's name.
            title: New title for the tool. Defaults to parent tool's title.
            transform_args: Optional transformations for parent tool arguments.
                Only specified arguments are transformed, others pass through unchanged:
                - Simple rename (str)
                - Complex transformation (rename/description/default/drop) (ArgTransform)
                - Drop the argument (None)
            description: New description. Defaults to parent's description.
            tags: New tags. Defaults to parent's tags.
            annotations: New annotations. Defaults to parent's annotations.
            output_schema: Control output schema for structured outputs:
                - None (default): Inherit from transform_fn if available, then parent tool
                - dict: Use custom output schema
                - False: Disable output schema and structured outputs
            serializer: New serializer. Defaults to parent's serializer.
            meta: Control meta information:
                - NotSet (default): Inherit from parent tool
                - dict: Use custom meta information
                - None: Remove meta information

        Returns:
            TransformedTool with the specified transformations.

        Examples:
            # Transform specific arguments only
            ```python
            Tool.from_tool(parent, transform_args={"old": "new"})  # Others unchanged
            ```

            # Custom function with partial transforms
            ```python
            async def custom(x: int, y: int) -> str:
                result = await forward(x=x, y=y)
                return f"Custom: {result}"

            Tool.from_tool(parent, transform_fn=custom, transform_args={"a": "x", "b": "y"})
            ```

            # Using **kwargs (gets all args, transformed and untransformed)
            ```python
            async def flexible(**kwargs) -> str:
                result = await forward(**kwargs)
                return f"Got: {kwargs}"

            Tool.from_tool(parent, transform_fn=flexible, transform_args={"a": "x"})
            ```

            # Control structured outputs and schemas
            ```python
            # Custom output schema
            Tool.from_tool(parent, output_schema={
                "type": "object",
                "properties": {"status": {"type": "string"}}
            })

            # Disable structured outputs
            Tool.from_tool(parent, output_schema=None)

            # Return ToolResult for full control
            async def custom_output(**kwargs) -> ToolResult:
                result = await forward(**kwargs)
                return ToolResult(
                    content=[TextContent(text="Summary")],
                    structured_content={"processed": True}
                )
            ```
        """
        transform_args = transform_args or {}

        if transform_fn is not None:
            parsed_fn = ParsedFunction.from_function(transform_fn, validate=False)
        else:
            parsed_fn = None

        # Validate transform_args
        parent_params = set(tool.parameters.get("properties", {}).keys())
        unknown_args = set(transform_args.keys()) - parent_params
        if unknown_args:
            raise ValueError(
                f"Unknown arguments in transform_args: {', '.join(sorted(unknown_args))}. "
                f"Parent tool `{tool.name}` has: {', '.join(sorted(parent_params))}"
            )

        # Always create the forwarding transform
        schema, forwarding_fn = cls._create_forwarding_transform(tool, transform_args)

        # Handle output schema
        if output_schema is NotSet:
            # Use smart fallback: try custom function, then parent
            if transform_fn is not None:
                # parsed fn is not none here
                final_output_schema = cast(ParsedFunction, parsed_fn).output_schema
                if final_output_schema is None:
                    # Check if function returns ToolResult - if so, don't fall back to parent
                    return_annotation = inspect.signature(
                        transform_fn
                    ).return_annotation
                    if return_annotation is ToolResult:
                        final_output_schema = None
                    else:
                        final_output_schema = tool.output_schema
            else:
                final_output_schema = tool.output_schema
        else:
            final_output_schema = cast(dict | None, output_schema)

        if transform_fn is None:
            # User wants pure transformation - use forwarding_fn as the main function
            final_fn = forwarding_fn
            final_schema = schema
        else:
            # parsed fn is not none here
            parsed_fn = cast(ParsedFunction, parsed_fn)
            # User provided custom function - merge schemas
            final_fn = transform_fn

            has_kwargs = cls._function_has_kwargs(transform_fn)

            # Validate function parameters against transformed schema
            fn_params = set(parsed_fn.input_schema.get("properties", {}).keys())
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
                    parsed_fn.input_schema, schema
                )
            else:
                # With **kwargs, function can access all transformed params
                # ArgTransform takes precedence over function signature
                # No validation needed - kwargs makes everything accessible

                # Start with function schema as base, then override with transformed schema
                final_schema = cls._merge_schema_with_precedence(
                    parsed_fn.input_schema, schema
                )

        # Additional validation: check for naming conflicts after transformation
        if transform_args:
            new_names = []
            for old_name, transform in transform_args.items():
                if not transform.hide:
                    if transform.name is not NotSet:
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

        final_name = name or tool.name
        final_description = (
            description if not isinstance(description, NotSetT) else tool.description
        )
        final_title = title if not isinstance(title, NotSetT) else tool.title
        final_meta = meta if not isinstance(meta, NotSetT) else tool.meta
        final_annotations = (
            annotations if not isinstance(annotations, NotSetT) else tool.annotations
        )
        final_serializer = (
            serializer if not isinstance(serializer, NotSetT) else tool.serializer
        )
        final_enabled = enabled if enabled is not None else tool.enabled

        transformed_tool = cls(
            fn=final_fn,
            forwarding_fn=forwarding_fn,
            parent_tool=tool,
            name=final_name,
            title=final_title,
            description=final_description,
            parameters=final_schema,
            output_schema=final_output_schema,
            tags=tags or tool.tags,
            annotations=final_annotations,
            serializer=final_serializer,
            meta=final_meta,
            transform_args=transform_args,
            enabled=final_enabled,
        )

        return transformed_tool

    @classmethod
    def _create_forwarding_transform(
        cls,
        parent_tool: Tool,
        transform_args: dict[str, ArgTransform] | None,
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
            - The new JSON schema for the transformed tool as a dictionary
            - Async function that validates and forwards calls to the parent tool
        """

        # Build transformed schema and mapping
        # Deep copy to prevent compress_schema from mutating parent tool's $defs
        parent_defs = deepcopy(parent_tool.parameters.get("$defs", {}))
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
                # Default behavior - pass through (no transformation)
                transform = ArgTransform()  # Default ArgTransform with no changes

            # Handle hidden parameters with defaults
            if transform.hide:
                # Validate that hidden parameters without user defaults have parent defaults
                has_user_default = (
                    transform.default is not NotSet
                    or transform.default_factory is not NotSet
                )
                if not has_user_default and old_name in parent_required:
                    raise ValueError(
                        f"Hidden parameter '{old_name}' has no default value in parent tool "
                        f"and no default or default_factory provided in ArgTransform. Either provide a default "
                        f"or default_factory in ArgTransform or don't hide required parameters."
                    )
                if has_user_default:
                    # Store info for later factory calling or direct value
                    hidden_defaults[old_name] = transform
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

        if parent_defs:
            schema["$defs"] = parent_defs
            schema = compress_schema(schema, prune_defs=True)

        # Create forwarding function that closes over everything it needs
        async def _forward(**kwargs: Any):
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
            for old_name, transform in hidden_defaults.items():
                if transform.default is not NotSet:
                    parent_args[old_name] = transform.default
                elif transform.default_factory is not NotSet:
                    # Type check to ensure default_factory is callable
                    if callable(transform.default_factory):
                        parent_args[old_name] = transform.default_factory()

            return await parent_tool.run(parent_args)

        return schema, _forward

    @staticmethod
    def _apply_single_transform(
        old_name: str,
        old_schema: dict[str, Any],
        transform: ArgTransform,
        is_required: bool,
    ) -> tuple[str, dict[str, Any], bool] | None:
        """Apply transformation to a single parameter.

        This method handles the transformation of a single argument according to
        the specified transformation rules.

        Args:
            old_name: Original name of the parameter.
            old_schema: Original JSON schema for the parameter.
            transform: ArgTransform object specifying how to transform the parameter.
            is_required: Whether the original parameter was required.

        Returns:
            Tuple of (new_name, new_schema, new_is_required) if parameter should be kept,
            None if parameter should be dropped.
        """
        if transform.hide:
            return None

        # Handle name transformation - ensure we always have a string
        if transform.name is not NotSet:
            new_name = transform.name if transform.name is not None else old_name
        else:
            new_name = old_name

        # Ensure new_name is always a string
        if not isinstance(new_name, str):
            new_name = old_name

        new_schema = old_schema.copy()

        # Handle description transformation
        if transform.description is not NotSet:
            if transform.description is None:
                new_schema.pop("description", None)  # Remove description
            else:
                new_schema["description"] = transform.description

        # Handle required transformation first
        if transform.required is not NotSet:
            is_required = bool(transform.required)
            if transform.required is True:
                # Remove any existing default when making required
                new_schema.pop("default", None)

        # Handle default value transformation (only if not making required)
        if transform.default is not NotSet and transform.required is not True:
            new_schema["default"] = transform.default
            is_required = False

        # Handle type transformation
        if transform.type is not NotSet:
            # Use TypeAdapter to get proper JSON schema for the type
            type_schema = get_cached_typeadapter(transform.type).json_schema()
            # Update the schema with the type information from TypeAdapter
            new_schema.update(type_schema)

        # Handle examples transformation
        if transform.examples is not NotSet:
            new_schema["examples"] = transform.examples

        return new_name, new_schema, is_required

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

        # Merge $defs from both schemas, with override taking precedence
        merged_defs = base_schema.get("$defs", {}).copy()
        override_defs = override_schema.get("$defs", {})

        for def_name, def_schema in override_defs.items():
            if def_name in merged_defs:
                base_def = merged_defs[def_name].copy()
                base_def.update(def_schema)
                merged_defs[def_name] = base_def
            else:
                merged_defs[def_name] = def_schema.copy()

        result = {
            "type": "object",
            "properties": merged_props,
            "required": list(final_required),
        }

        if merged_defs:
            result["$defs"] = merged_defs
            result = compress_schema(result, prune_defs=True)

        return result

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


class ToolTransformConfig(FastMCPBaseModel):
    """Provides a way to transform a tool."""

    name: str | None = Field(default=None, description="The new name for the tool.")

    title: str | None = Field(
        default=None,
        description="The new title of the tool.",
    )
    description: str | None = Field(
        default=None,
        description="The new description of the tool.",
    )
    tags: Annotated[set[str], BeforeValidator(_convert_set_default_none)] = Field(
        default_factory=set,
        description="The new tags for the tool.",
    )
    meta: dict[str, Any] | None = Field(
        default=None,
        description="The new meta information for the tool.",
    )

    enabled: bool = Field(
        default=True,
        description="Whether the tool is enabled.",
    )

    arguments: dict[str, ArgTransformConfig] = Field(
        default_factory=dict,
        description="A dictionary of argument transforms to apply to the tool.",
    )

    def apply(self, tool: Tool) -> TransformedTool:
        """Create a TransformedTool from a provided tool and this transformation configuration."""

        tool_changes: dict[str, Any] = self.model_dump(
            exclude_unset=True, exclude={"arguments"}
        )

        return TransformedTool.from_tool(
            tool=tool,
            **tool_changes,
            transform_args={k: v.to_arg_transform() for k, v in self.arguments.items()},
        )


def apply_transformations_to_tools(
    tools: dict[str, Tool],
    transformations: dict[str, ToolTransformConfig],
) -> dict[str, Tool]:
    """Apply a list of transformations to a list of tools. Tools that do not have any transformations
    are left unchanged.
    """

    transformed_tools: dict[str, Tool] = {}

    for tool_name, tool in tools.items():
        if transformation := transformations.get(tool_name):
            transformed_tools[transformation.name or tool_name] = transformation.apply(
                tool
            )
            continue

        transformed_tools[tool_name] = tool

    return transformed_tools
