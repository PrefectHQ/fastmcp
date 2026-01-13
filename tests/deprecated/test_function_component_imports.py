"""Test that deprecated import paths for function components still work."""

import warnings

import pytest

from fastmcp.utilities.tests import temporary_settings


class TestDeprecatedFunctionToolImports:
    def test_function_tool_from_tool_module(self):
        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(
                DeprecationWarning, match="Import from fastmcp.tools.function_tool"
            ):
                from fastmcp.tools.tool import FunctionTool

            # Verify it's the real class
            from fastmcp.tools.function_tool import (
                FunctionTool as CanonicalFunctionTool,
            )

            assert FunctionTool is CanonicalFunctionTool

    def test_parsed_function_from_tool_module(self):
        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(
                DeprecationWarning, match="Import from fastmcp.tools.function_tool"
            ):
                from fastmcp.tools.tool import ParsedFunction

            from fastmcp.tools.function_tool import (
                ParsedFunction as CanonicalParsedFunction,
            )

            assert ParsedFunction is CanonicalParsedFunction

    def test_tool_decorator_from_tool_module(self):
        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(
                DeprecationWarning, match="Import from fastmcp.tools.function_tool"
            ):
                from fastmcp.tools.tool import tool

            from fastmcp.tools.function_tool import tool as canonical_tool

            assert tool is canonical_tool

    def test_no_warning_when_disabled(self):
        with temporary_settings(deprecation_warnings=False):
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                from fastmcp.tools.tool import FunctionTool  # noqa: F401


class TestDeprecatedFunctionResourceImports:
    def test_function_resource_from_resource_module(self):
        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(
                DeprecationWarning,
                match="Import from fastmcp.resources.function_resource",
            ):
                from fastmcp.resources.resource import FunctionResource

            from fastmcp.resources.function_resource import (
                FunctionResource as CanonicalFunctionResource,
            )

            assert FunctionResource is CanonicalFunctionResource

    def test_resource_decorator_from_resource_module(self):
        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(
                DeprecationWarning,
                match="Import from fastmcp.resources.function_resource",
            ):
                from fastmcp.resources.resource import resource

            from fastmcp.resources.function_resource import (
                resource as canonical_resource,
            )

            assert resource is canonical_resource

    def test_no_warning_when_disabled(self):
        with temporary_settings(deprecation_warnings=False):
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                from fastmcp.resources.resource import FunctionResource  # noqa: F401


class TestDeprecatedFunctionPromptImports:
    def test_function_prompt_from_prompt_module(self):
        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(
                DeprecationWarning, match="Import from fastmcp.prompts.function_prompt"
            ):
                from fastmcp.prompts.prompt import FunctionPrompt

            from fastmcp.prompts.function_prompt import (
                FunctionPrompt as CanonicalFunctionPrompt,
            )

            assert FunctionPrompt is CanonicalFunctionPrompt

    def test_prompt_decorator_from_prompt_module(self):
        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(
                DeprecationWarning, match="Import from fastmcp.prompts.function_prompt"
            ):
                from fastmcp.prompts.prompt import prompt

            from fastmcp.prompts.function_prompt import prompt as canonical_prompt

            assert prompt is canonical_prompt

    def test_no_warning_when_disabled(self):
        with temporary_settings(deprecation_warnings=False):
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                from fastmcp.prompts.prompt import FunctionPrompt  # noqa: F401
