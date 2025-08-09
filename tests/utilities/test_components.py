"""Tests for fastmcp.utilities.components module."""

import pytest
from pydantic import ValidationError

from fastmcp.utilities.components import (
    FastMCPComponent,
    FastMCPMeta,
    MirroredComponent,
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
            tags=["test", "component"],
        )

    def test_initialization_with_minimal_params(self):
        """Test component initialization with minimal parameters."""
        component = FastMCPComponent(name="minimal")
        assert component.name == "minimal"
        assert component.title is None
        assert component.description is None
        assert component.tags == set()
        assert component.meta is None
        assert component.enabled is True

    def test_initialization_with_all_params(self):
        """Test component initialization with all parameters."""
        meta = {"custom": "value"}
        component = FastMCPComponent(
            name="full",
            title="Full Component",
            description="A fully configured component",
            tags=["tag1", "tag2"],
            meta=meta,
            enabled=False,
        )
        assert component.name == "full"
        assert component.title == "Full Component"
        assert component.description == "A fully configured component"
        assert component.tags == {"tag1", "tag2"}
        assert component.meta == meta
        assert component.enabled is False

    def test_key_property_without_custom_key(self, basic_component):
        """Test that key property returns name when no custom key is set."""
        assert basic_component.key == "test_component"

    def test_key_property_with_custom_key(self):
        """Test that key property returns custom key when set."""
        component = FastMCPComponent(name="test", key="custom_key")
        assert component.key == "custom_key"
        assert component.name == "test"

    def test_get_meta_without_fastmcp_meta(self, basic_component):
        """Test get_meta without including fastmcp meta."""
        basic_component.meta = {"custom": "data"}
        result = basic_component.get_meta(include_fastmcp_meta=False)
        assert result == {"custom": "data"}
        assert "_fastmcp" not in result

    def test_get_meta_with_fastmcp_meta(self, basic_component):
        """Test get_meta including fastmcp meta."""
        basic_component.meta = {"custom": "data"}
        basic_component.tags = {"tag2", "tag1"}  # Unordered to test sorting
        result = basic_component.get_meta(include_fastmcp_meta=True)
        assert result["custom"] == "data"
        assert "_fastmcp" in result
        assert result["_fastmcp"]["tags"] == ["tag1", "tag2"]  # Should be sorted

    def test_get_meta_preserves_existing_fastmcp_meta(self):
        """Test that get_meta preserves existing _fastmcp meta."""
        component = FastMCPComponent(
            name="test",
            meta={"_fastmcp": {"existing": "value"}},
            tags=["new_tag"],
        )
        result = component.get_meta(include_fastmcp_meta=True)
        assert result is not None
        assert result["_fastmcp"]["existing"] == "value"
        assert result["_fastmcp"]["tags"] == ["new_tag"]

    def test_get_meta_returns_none_when_empty(self):
        """Test that get_meta returns None when no meta and fastmcp_meta is False."""
        component = FastMCPComponent(name="test")
        result = component.get_meta(include_fastmcp_meta=False)
        assert result is None

    def test_model_copy_creates_copy_with_new_key(self, basic_component):
        """Test that model_copy with key creates a copy with a new key."""
        new_component = basic_component.model_copy(key="new_key")
        assert new_component.key == "new_key"
        assert new_component.name == basic_component.name
        assert new_component is not basic_component  # Should be a copy
        assert basic_component.key == "test_component"  # Original unchanged

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

    def test_enable_method(self):
        """Test enable method."""
        component = FastMCPComponent(name="test", enabled=False)
        assert component.enabled is False
        component.enable()
        assert component.enabled is True

    def test_disable_method(self):
        """Test disable method."""
        component = FastMCPComponent(name="test", enabled=True)
        assert component.enabled is True
        component.disable()
        assert component.enabled is False

    def test_copy_method(self, basic_component):
        """Test copy method creates an independent copy."""
        copy = basic_component.copy()
        assert copy == basic_component
        assert copy is not basic_component

        # Modify copy and ensure original is unchanged
        copy.name = "modified"
        assert basic_component.name == "test_component"

    def test_tags_deduplication(self):
        """Test that tags are deduplicated."""
        component = FastMCPComponent(
            name="test",
            tags=["tag1", "tag2", "tag1", "tag2"],
        )
        assert component.tags == {"tag1", "tag2"}

    def test_validation_error_for_invalid_data(self):
        """Test that validation errors are raised for invalid data."""
        with pytest.raises(ValidationError):
            FastMCPComponent()  # Missing required name field

    def test_extra_fields_forbidden(self):
        """Test that extra fields are not allowed."""
        with pytest.raises(ValidationError) as exc_info:
            FastMCPComponent(name="test", unknown_field="value")
        assert "Extra inputs are not permitted" in str(exc_info.value)


class TestMirroredComponent:
    """Tests for the MirroredComponent class."""

    @pytest.fixture
    def mirrored_component(self):
        """Create a mirrored component for testing."""
        return MirroredComponent(
            name="mirrored",
            description="A mirrored component",
            _mirrored=True,
        )

    @pytest.fixture
    def non_mirrored_component(self):
        """Create a non-mirrored component for testing."""
        return MirroredComponent(
            name="local",
            description="A local component",
            _mirrored=False,
        )

    def test_initialization_mirrored(self, mirrored_component):
        """Test initialization of a mirrored component."""
        assert mirrored_component.name == "mirrored"
        assert mirrored_component._mirrored is True

    def test_initialization_non_mirrored(self, non_mirrored_component):
        """Test initialization of a non-mirrored component."""
        assert non_mirrored_component.name == "local"
        assert non_mirrored_component._mirrored is False

    def test_enable_raises_error_when_mirrored(self, mirrored_component):
        """Test that enable raises an error for mirrored components."""
        with pytest.raises(RuntimeError) as exc_info:
            mirrored_component.enable()
        assert "Cannot enable mirrored component" in str(exc_info.value)
        assert "mirrored" in str(exc_info.value)
        assert ".copy()" in str(exc_info.value)

    def test_disable_raises_error_when_mirrored(self, mirrored_component):
        """Test that disable raises an error for mirrored components."""
        with pytest.raises(RuntimeError) as exc_info:
            mirrored_component.disable()
        assert "Cannot disable mirrored component" in str(exc_info.value)
        assert "mirrored" in str(exc_info.value)
        assert ".copy()" in str(exc_info.value)

    def test_enable_works_when_not_mirrored(self, non_mirrored_component):
        """Test that enable works for non-mirrored components."""
        non_mirrored_component.enabled = False
        non_mirrored_component.enable()
        assert non_mirrored_component.enabled is True

    def test_disable_works_when_not_mirrored(self, non_mirrored_component):
        """Test that disable works for non-mirrored components."""
        non_mirrored_component.enabled = True
        non_mirrored_component.disable()
        assert non_mirrored_component.enabled is False

    def test_copy_removes_mirrored_flag(self, mirrored_component):
        """Test that copy creates a non-mirrored version."""
        copy = mirrored_component.copy()
        assert copy._mirrored is False
        assert copy.name == mirrored_component.name
        assert copy is not mirrored_component

        # Should be able to enable/disable the copy
        copy.enable()
        copy.disable()
        assert copy.enabled is False

    def test_copy_preserves_non_mirrored_state(self, non_mirrored_component):
        """Test that copy preserves non-mirrored state."""
        copy = non_mirrored_component.copy()
        assert copy._mirrored is False
        assert copy == non_mirrored_component
        assert copy is not non_mirrored_component

    def test_inheritance_from_fastmcp_component(self):
        """Test that MirroredComponent inherits from FastMCPComponent."""
        component = MirroredComponent(name="test")
        assert isinstance(component, FastMCPComponent)
        assert isinstance(component, MirroredComponent)

    def test_all_fastmcp_component_features_work(self, mirrored_component):
        """Test that all FastMCPComponent features work except enable/disable."""
        # Test key property
        assert mirrored_component.key == "mirrored"

        # Test model_copy with key
        with_key = mirrored_component.model_copy(key="new_key")
        assert with_key.key == "new_key"

        # Test get_meta
        mirrored_component.tags = {"tag1"}
        meta = mirrored_component.get_meta(include_fastmcp_meta=True)
        assert meta["_fastmcp"]["tags"] == ["tag1"]

        # Test repr
        repr_str = repr(mirrored_component)
        assert "MirroredComponent" in repr_str


class TestFastMCPMeta:
    """Tests for the FastMCPMeta TypedDict."""

    def test_fastmcp_meta_structure(self):
        """Test that FastMCPMeta has the expected structure."""
        meta: FastMCPMeta = {"tags": ["tag1", "tag2"]}
        assert meta["tags"] == ["tag1", "tag2"]

    def test_fastmcp_meta_optional_fields(self):
        """Test that FastMCPMeta fields are optional."""
        meta: FastMCPMeta = {}
        assert "tags" not in meta  # Should be optional


class TestEdgeCasesAndIntegration:
    """Tests for edge cases and integration scenarios."""

    def test_empty_tags_conversion(self):
        """Test that empty tags are handled correctly."""
        component = FastMCPComponent(name="test", tags=[])
        assert component.tags == set()

    def test_tags_with_none_values(self):
        """Test tags behavior with various input types."""
        # Test with None (through validator)
        component = FastMCPComponent(name="test")
        assert component.tags == set()

    def test_meta_mutation_affects_original(self):
        """Test that get_meta returns a reference to the original meta."""
        component = FastMCPComponent(name="test", meta={"key": "value"})
        meta = component.get_meta(include_fastmcp_meta=False)
        assert meta is not None
        meta["key"] = "modified"
        assert component.meta is not None
        assert component.meta["key"] == "modified"  # Original is modified

        # This is the actual behavior - get_meta returns a reference

    def test_component_with_complex_meta(self):
        """Test component with nested meta structures."""
        complex_meta = {
            "nested": {"level1": {"level2": "value"}},
            "list": [1, 2, 3],
            "bool": True,
        }
        component = FastMCPComponent(name="test", meta=complex_meta)
        assert component.meta == complex_meta

    def test_model_copy_with_key_preserves_all_attributes(self):
        """Test that model_copy with key preserves all component attributes."""
        component = FastMCPComponent(
            name="test",
            title="Title",
            description="Description",
            tags=["tag1", "tag2"],
            meta={"key": "value"},
            enabled=False,
        )
        new_component = component.model_copy(key="new_key")

        assert new_component.name == component.name
        assert new_component.title == component.title
        assert new_component.description == component.description
        assert new_component.tags == component.tags
        assert new_component.meta == component.meta
        assert new_component.enabled == component.enabled
        assert new_component.key == "new_key"

    def test_mirrored_component_copy_chain(self):
        """Test creating copies of copies for mirrored components."""
        original = MirroredComponent(name="original", _mirrored=True)
        copy1 = original.copy()
        copy2 = copy1.copy()

        assert original._mirrored is True
        assert copy1._mirrored is False
        assert copy2._mirrored is False

        # All copies should be independent
        copy1.name = "copy1"
        copy2.name = "copy2"
        assert original.name == "original"
        assert copy1.name == "copy1"
        assert copy2.name == "copy2"

    def test_model_copy_with_update_and_key(self):
        """Test that model_copy works with both update dict and key parameter."""
        component = FastMCPComponent(
            name="test",
            title="Original Title",
            description="Original Description",
            tags=["tag1"],
            enabled=True,
        )

        # Test with both update and key
        updated_component = component.model_copy(
            update={"title": "New Title", "description": "New Description"},
            key="new_key",
        )

        assert updated_component.name == "test"  # Not in update, unchanged
        assert updated_component.title == "New Title"  # Updated
        assert updated_component.description == "New Description"  # Updated
        assert updated_component.tags == {"tag1"}  # Not in update, unchanged
        assert updated_component.enabled is True  # Not in update, unchanged
        assert updated_component.key == "new_key"  # Custom key set

        # Original should be unchanged
        assert component.title == "Original Title"
        assert component.description == "Original Description"
        assert component.key == "test"  # Uses name as key

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
