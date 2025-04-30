from typing import Annotated, Any

import pytest

from fastmcp.utilities.types import Image, is_class_member_of_type, issubclass_safe


class BaseClass:
    pass


class ChildClass(BaseClass):
    pass


class OtherClass:
    pass


class TestIsClassMemberOfType:
    def test_basic_subclass_check(self):
        """Test that a subclass is recognized as a member of the base class."""
        assert is_class_member_of_type(ChildClass, BaseClass)

    def test_self_is_member(self):
        """Test that a class is a member of itself."""
        assert is_class_member_of_type(BaseClass, BaseClass)

    def test_unrelated_class_is_not_member(self):
        """Test that an unrelated class is not a member of the base class."""
        assert not is_class_member_of_type(OtherClass, BaseClass)

    def test_typing_union_with_member_is_member(self):
        """Test that Union type with a member class is detected as a member."""
        union_type1: Any = ChildClass | OtherClass
        union_type2: Any = OtherClass | ChildClass

        assert is_class_member_of_type(union_type1, BaseClass)
        assert is_class_member_of_type(union_type2, BaseClass)

    def test_typing_union_without_member_is_not_member(self):
        """Test that Union type without any member class is not a member."""
        union_type: Any = OtherClass | str
        assert not is_class_member_of_type(union_type, BaseClass)

    def test_pipe_union_with_member_is_member(self):
        """Test that pipe syntax union with a member class is detected as a member."""
        union_pipe1: Any = ChildClass | OtherClass
        union_pipe2: Any = OtherClass | ChildClass

        assert is_class_member_of_type(union_pipe1, BaseClass)
        assert is_class_member_of_type(union_pipe2, BaseClass)

    def test_pipe_union_without_member_is_not_member(self):
        """Test that pipe syntax union without any member class is not a member."""
        union_pipe: Any = OtherClass | str
        assert not is_class_member_of_type(union_pipe, BaseClass)

    def test_annotated_member_is_member(self):
        """Test that Annotated with a member class is detected as a member."""
        annotated1: Any = Annotated[ChildClass, "metadata"]
        annotated2: Any = Annotated[BaseClass, "metadata"]

        assert is_class_member_of_type(annotated1, BaseClass)
        assert is_class_member_of_type(annotated2, BaseClass)

    def test_annotated_non_member_is_not_member(self):
        """Test that Annotated with a non-member class is not a member."""
        annotated: Any = Annotated[OtherClass, "metadata"]
        assert not is_class_member_of_type(annotated, BaseClass)

    def test_annotated_with_union_member_is_member(self):
        """Test that Annotated with a Union containing a member class is a member."""
        # Test with both Union styles
        annotated1: Any = Annotated[ChildClass | OtherClass, "metadata"]
        annotated2: Any = Annotated[ChildClass | OtherClass, "metadata"]

        assert is_class_member_of_type(annotated1, BaseClass)
        assert is_class_member_of_type(annotated2, BaseClass)

    def test_nested_annotated_with_member_is_member(self):
        """Test that nested Annotated with a member class is a member."""
        annotated: Any = Annotated[Annotated[ChildClass, "inner"], "outer"]
        assert is_class_member_of_type(annotated, BaseClass)

    def test_none_is_not_member(self):
        """Test that None is not a member of any class."""
        assert not is_class_member_of_type(None, BaseClass)  # type: ignore

    def test_generic_type_is_not_member(self):
        """Test that generic types are not members based on their parameter types."""
        list_type: Any = list[ChildClass]
        assert not is_class_member_of_type(list_type, BaseClass)


class TestIsSubclassSafe:
    def test_child_is_subclass_of_parent(self):
        """Test that a child class is recognized as a subclass of its parent."""
        assert issubclass_safe(ChildClass, BaseClass)

    def test_class_is_subclass_of_itself(self):
        """Test that a class is a subclass of itself."""
        assert issubclass_safe(BaseClass, BaseClass)

    def test_unrelated_class_is_not_subclass(self):
        """Test that an unrelated class is not a subclass."""
        assert not issubclass_safe(OtherClass, BaseClass)

    def test_none_type_handled_safely(self):
        """Test that None type is handled safely without raising TypeError."""
        assert not issubclass_safe(None, BaseClass)  # type: ignore


class TestImage:
    def test_image_initialization_with_path(self):
        """Test image initialization with a path."""
        # Mock test - we're not actually going to read a file
        image = Image(path="test.png")
        assert image.path is not None
        assert image.data is None
        assert image._mime_type == "image/png"

    def test_image_initialization_with_data(self):
        """Test image initialization with data."""
        image = Image(data=b"test")
        assert image.path is None
        assert image.data == b"test"
        assert image._mime_type == "image/png"  # Default for raw data

    def test_image_initialization_with_format(self):
        """Test image initialization with a specific format."""
        image = Image(data=b"test", format="jpeg")
        assert image._mime_type == "image/jpeg"

    def test_missing_data_and_path_raises_error(self):
        """Test that error is raised when neither path nor data is provided."""
        with pytest.raises(ValueError, match="Either path or data must be provided"):
            Image()

    def test_both_data_and_path_raises_error(self):
        """Test that error is raised when both path and data are provided."""
        with pytest.raises(
            ValueError, match="Only one of path or data can be provided"
        ):
            Image(path="test.png", data=b"test")
