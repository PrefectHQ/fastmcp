"""Tests for fastmcp.utilities.components module."""

import warnings

import pytest
from pydantic import ValidationError

from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.tools.tool import Tool
from fastmcp.utilities.components import (
    FastMCPComponent,
    FastMCPMeta,
    _convert_set_default_none,
)


class TestConvertSetDefaultNone:
    """Tests for the _convert_set_default_none helper function."""

    def test_none_returns_empty_set(self):
        """Test that None returns an empty set."""
        result = _convert_set_default_none(None)
        assert result == set()

    def test_set_returns_same_set(self):
        """Test that a set returns the same set."""
        test_set = {"tag1", "tag2"}
        result = _convert_set_default_none(test_set)
        assert result == test_set

    def test_list_converts_to_set(self):
        """Test that a list converts to a set."""
        test_list = ["tag1", "tag2", "tag1"]  # Duplicate to test deduplication
        result = _convert_set_default_none(test_list)
        assert result == {"tag1", "tag2"}

    def test_tuple_converts_to_set(self):
        """Test that a tuple converts to a set."""
        test_tuple = ("tag1", "tag2")
        result = _convert_set_default_none(test_tuple)
        assert result == {"tag1", "tag2"}


class TestFastMCPComponent:
    """Tests for the FastMCPComponent class."""

    @pytest.fixture
    def basic_component(self):
        """Create a basic component for testing."""
        return FastMCPComponent(
            name="test_component",
            title="Test Component",
            description="A test component",
            tags={"test", "component"},
        )

    def test_initialization_with_minimal_params(self):
        """Test component initialization with minimal parameters."""
        component = FastMCPComponent(name="minimal")
        assert component.name == "minimal"
        assert component.title is None
        assert component.description is None
        assert component.tags == set()
        assert component.meta is None

    def test_initialization_with_all_params(self):
        """Test component initialization with all parameters."""
        meta = {"custom": "value"}
        component = FastMCPComponent(
            name="full",
            title="Full Component",
            description="A fully configured component",
            tags={"tag1", "tag2"},
            meta=meta,
        )
        assert component.name == "full"
        assert component.title == "Full Component"
        assert component.description == "A fully configured component"
        assert component.tags == {"tag1", "tag2"}
        assert component.meta == meta

    def test_key_property_without_custom_key(self, basic_component):
        """Test that key property returns name@version when no custom key is set."""
        # Base component has no KEY_PREFIX, so key is just "name@version" (or "name@" for unversioned)
        assert basic_component.key == "test_component@"

    def test_get_meta_with_fastmcp_meta(self, basic_component):
        """Test get_meta always includes fastmcp meta."""
        basic_component.meta = {"custom": "data"}
        basic_component.tags = {"tag2", "tag1"}  # Unordered to test sorting
        result = basic_component.get_meta()
        assert result["custom"] == "data"
        assert "fastmcp" in result
        assert result["fastmcp"]["tags"] == ["tag1", "tag2"]  # Should be sorted

    def test_get_meta_preserves_existing_fastmcp_meta(self):
        """Test that get_meta preserves existing fastmcp meta."""
        component = FastMCPComponent(
            name="test",
            meta={"fastmcp": {"existing": "value"}},
            tags={"new_tag"},
        )
        result = component.get_meta()
        assert result is not None
        assert result["fastmcp"]["existing"] == "value"
        assert result["fastmcp"]["tags"] == ["new_tag"]

    def test_get_meta_returns_dict_with_fastmcp_when_empty(self):
        """Test that get_meta returns dict with fastmcp meta even when no custom meta."""
        component = FastMCPComponent(name="test")
        result = component.get_meta()
        assert result is not None
        assert "fastmcp" in result
        assert result["fastmcp"]["tags"] == []

    def test_get_meta_includes_version(self):
        """Test that get_meta includes version when component has a version."""
        component = FastMCPComponent(name="test", version="v1.0.0", tags={"tag1"})
        result = component.get_meta()
        assert result is not None
        assert result["fastmcp"]["version"] == "v1.0.0"
        assert result["fastmcp"]["tags"] == ["tag1"]

    def test_get_meta_excludes_version_when_none(self):
        """Test that get_meta excludes version when component has no version."""
        component = FastMCPComponent(name="test", tags={"tag1"})
        result = component.get_meta()
        assert result is not None
        assert "version" not in result["fastmcp"]
        assert result["fastmcp"]["tags"] == ["tag1"]

    def test_equality_same_components(self):
        """Test that identical components are equal."""
        comp1 = FastMCPComponent(name="test", description="desc")
        comp2 = FastMCPComponent(name="test", description="desc")
        assert comp1 == comp2

    def test_equality_different_components(self):
        """Test that different components are not equal."""
        comp1 = FastMCPComponent(name="test1")
        comp2 = FastMCPComponent(name="test2")
        assert comp1 != comp2

    def test_equality_different_types(self, basic_component):
        """Test that component is not equal to other types."""
        assert basic_component != "not a component"
        assert basic_component != 123
        assert basic_component is not None

    def test_repr(self, basic_component):
        """Test string representation of component."""
        repr_str = repr(basic_component)
        assert "FastMCPComponent" in repr_str
        assert "name='test_component'" in repr_str
        assert "title='Test Component'" in repr_str
        assert "description='A test component'" in repr_str

    def test_copy_method(self, basic_component):
        """Test copy method creates an independent copy."""
        copy = basic_component.copy()
        assert copy == basic_component
        assert copy is not basic_component

        # Modify copy and ensure original is unchanged
        copy.name = "modified"
        assert basic_component.name == "test_component"

    def test_tags_deduplication(self):
        """Test that tags are deduplicated when passed as a sequence."""
        component = FastMCPComponent(
            name="test",
            tags=["tag1", "tag2", "tag1", "tag2"],  # type: ignore[arg-type]
        )
        assert component.tags == {"tag1", "tag2"}

    def test_validation_error_for_invalid_data(self):
        """Test that validation errors are raised for invalid data."""
        with pytest.raises(ValidationError):
            FastMCPComponent()  # type: ignore[call-arg]

    def test_extra_fields_forbidden(self):
        """Test that extra fields are not allowed."""
        with pytest.raises(ValidationError) as exc_info:
            FastMCPComponent(name="test", unknown_field="value")  # type: ignore[call-arg]  # Intentionally passing invalid field for test
        assert "Extra inputs are not permitted" in str(exc_info.value)


class TestKeyPrefix:
    """Tests for KEY_PREFIX and make_key functionality."""

    def test_base_class_has_empty_prefix(self):
        """Test that FastMCPComponent has empty KEY_PREFIX."""
        assert FastMCPComponent.KEY_PREFIX == ""

    def test_make_key_without_prefix(self):
        """Test make_key returns just identifier when KEY_PREFIX is empty."""
        assert FastMCPComponent.make_key("my_name") == "my_name"

    def test_tool_has_tool_prefix(self):
        """Test that Tool has 'tool' KEY_PREFIX."""
        assert Tool.KEY_PREFIX == "tool"
        assert Tool.make_key("my_tool") == "tool:my_tool"

    def test_resource_has_resource_prefix(self):
        """Test that Resource has 'resource' KEY_PREFIX."""
        assert Resource.KEY_PREFIX == "resource"
        assert Resource.make_key("file://test.txt") == "resource:file://test.txt"

    def test_template_has_template_prefix(self):
        """Test that ResourceTemplate has 'template' KEY_PREFIX."""
        assert ResourceTemplate.KEY_PREFIX == "template"
        assert ResourceTemplate.make_key("data://{id}") == "template:data://{id}"

    def test_prompt_has_prompt_prefix(self):
        """Test that Prompt has 'prompt' KEY_PREFIX."""
        assert Prompt.KEY_PREFIX == "prompt"
        assert Prompt.make_key("my_prompt") == "prompt:my_prompt"

    def test_tool_key_property(self):
        """Test that Tool.key returns prefixed key with version sentinel."""
        tool = Tool(name="greet", description="A greeting tool", parameters={})
        assert tool.key == "tool:greet@"

    def test_prompt_key_property(self):
        """Test that Prompt.key returns prefixed key with version sentinel."""
        prompt = Prompt(name="analyze", description="An analysis prompt")
        assert prompt.key == "prompt:analyze@"

    def test_warning_for_missing_key_prefix(self):
        """Test that subclassing without KEY_PREFIX emits a warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            class NoPrefix(FastMCPComponent):
                pass

            assert len(w) == 1
            assert "NoPrefix does not define KEY_PREFIX" in str(w[0].message)

    def test_no_warning_when_key_prefix_defined(self):
        """Test that subclassing with KEY_PREFIX does not emit a warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            class WithPrefix(FastMCPComponent):
                KEY_PREFIX = "custom"

            assert len(w) == 0
            assert WithPrefix.make_key("test") == "custom:test"


class TestComponentEnableDisable:
    """Tests for the enable/disable methods raising NotImplementedError."""

    def test_enable_raises_not_implemented_error(self):
        """Test that enable() raises NotImplementedError with migration guidance."""
        component = FastMCPComponent(name="test")
        with pytest.raises(NotImplementedError) as exc_info:
            component.enable()
        assert "server.enable" in str(exc_info.value)
        assert "test" in str(exc_info.value)

    def test_disable_raises_not_implemented_error(self):
        """Test that disable() raises NotImplementedError with migration guidance."""
        component = FastMCPComponent(name="test")
        with pytest.raises(NotImplementedError) as exc_info:
            component.disable()
        assert "server.disable" in str(exc_info.value)
        assert "test" in str(exc_info.value)

    def test_tool_enable_raises_not_implemented(self):
        """Test that Tool.enable() raises NotImplementedError."""
        tool = Tool(name="my_tool", description="A tool", parameters={})
        with pytest.raises(NotImplementedError) as exc_info:
            tool.enable()
        assert "tool:my_tool@" in str(exc_info.value)

    def test_tool_disable_raises_not_implemented(self):
        """Test that Tool.disable() raises NotImplementedError."""
        tool = Tool(name="my_tool", description="A tool", parameters={})
        with pytest.raises(NotImplementedError) as exc_info:
            tool.disable()
        assert "tool:my_tool@" in str(exc_info.value)

    def test_prompt_enable_raises_not_implemented(self):
        """Test that Prompt.enable() raises NotImplementedError."""
        prompt = Prompt(name="my_prompt", description="A prompt")
        with pytest.raises(NotImplementedError) as exc_info:
            prompt.enable()
        assert "prompt:my_prompt@" in str(exc_info.value)


class TestFastMCPMeta:
    """Tests for the FastMCPMeta TypedDict."""

    def test_fastmcp_meta_structure(self):
        """Test that FastMCPMeta has the expected structure."""
        meta: FastMCPMeta = {"tags": ["tag1", "tag2"]}
        assert meta["tags"] == ["tag1", "tag2"]

    def test_fastmcp_meta_with_version(self):
        """Test that FastMCPMeta can include version."""
        meta: FastMCPMeta = {"tags": ["tag1"], "version": "v1.0.0"}
        assert meta["tags"] == ["tag1"]
        assert meta["version"] == "v1.0.0"

    def test_fastmcp_meta_optional_fields(self):
        """Test that FastMCPMeta fields are optional."""
        meta: FastMCPMeta = {}
        assert "tags" not in meta  # Should be optional
        assert "version" not in meta  # Should be optional


class TestEdgeCasesAndIntegration:
    """Tests for edge cases and integration scenarios."""

    def test_empty_tags_conversion(self):
        """Test that empty tags are handled correctly."""
        component = FastMCPComponent(name="test", tags=set())
        assert component.tags == set()

    def test_tags_with_none_values(self):
        """Test tags behavior with various input types."""
        # Test with None (through validator)
        component = FastMCPComponent(name="test")
        assert component.tags == set()

    def test_get_meta_returns_copy(self):
        """Test that get_meta returns a copy, not a reference to the original."""
        component = FastMCPComponent(name="test", meta={"key": "value"})
        meta = component.get_meta()
        assert meta is not None
        meta["key"] = "modified"
        assert component.meta is not None
        # get_meta returns a copy - mutating it doesn't affect the original
        assert component.meta["key"] == "value"

    def test_component_with_complex_meta(self):
        """Test component with nested meta structures."""
        complex_meta = {
            "nested": {"level1": {"level2": "value"}},
            "list": [1, 2, 3],
            "bool": True,
        }
        component = FastMCPComponent(name="test", meta=complex_meta)
        assert component.meta == complex_meta

    def test_model_copy_preserves_all_attributes(self):
        """Test that model_copy preserves all component attributes."""
        component = FastMCPComponent(
            name="test",
            title="Title",
            description="Description",
            tags={"tag1", "tag2"},
            meta={"key": "value"},
        )
        new_component = component.model_copy()

        assert new_component.name == component.name
        assert new_component.title == component.title
        assert new_component.description == component.description
        assert new_component.tags == component.tags
        assert new_component.meta == component.meta
        assert new_component.key == component.key

    def test_model_copy_with_update(self):
        """Test that model_copy works with update dict."""
        component = FastMCPComponent(
            name="test",
            title="Original Title",
            description="Original Description",
            tags={"tag1"},
        )

        # Test with update (including name which affects .key)
        updated_component = component.model_copy(
            update={
                "name": "new_name",
                "title": "New Title",
                "description": "New Description",
            },
        )

        assert updated_component.name == "new_name"  # Updated
        assert updated_component.title == "New Title"  # Updated
        assert updated_component.description == "New Description"  # Updated
        assert updated_component.tags == {"tag1"}  # Not in update, unchanged
        assert (
            updated_component.key == "new_name@"
        )  # .key is computed from name with @ sentinel

        # Original should be unchanged
        assert component.name == "test"
        assert component.title == "Original Title"
        assert component.description == "Original Description"
        assert component.key == "test@"  # Uses name as key with @ sentinel

    def test_model_copy_deep_parameter(self):
        """Test that model_copy respects the deep parameter."""
        nested_dict = {"nested": {"value": 1}}
        component = FastMCPComponent(name="test", meta=nested_dict)

        # Shallow copy (default)
        shallow_copy = component.model_copy()
        assert shallow_copy.meta is not None
        assert component.meta is not None
        shallow_copy.meta["nested"]["value"] = 2
        assert component.meta["nested"]["value"] == 2  # Original affected

        # Deep copy
        component.meta["nested"]["value"] = 1  # Reset
        deep_copy = component.model_copy(deep=True)
        assert deep_copy.meta is not None
        deep_copy.meta["nested"]["value"] = 3
        assert component.meta["nested"]["value"] == 1  # Original unaffected
