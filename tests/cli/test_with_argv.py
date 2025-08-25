"""Test the with_argv context manager."""

import sys
from unittest.mock import patch

import pytest

from fastmcp.cli.cli import with_argv


class TestWithArgv:
    """Test the with_argv context manager."""

    def test_with_argv_replaces_args(self):
        """Test that with_argv properly replaces sys.argv."""
        original_argv = sys.argv[:]
        test_args = ["--name", "TestServer", "--debug"]

        with with_argv(test_args):
            # Should preserve script name and add new args
            assert sys.argv[0] == original_argv[0]
            assert sys.argv[1:] == test_args

        # Should restore original argv after context
        assert sys.argv == original_argv

    def test_with_argv_none_does_nothing(self):
        """Test that with_argv(None) doesn't change sys.argv."""
        original_argv = sys.argv[:]

        with with_argv(None):
            assert sys.argv == original_argv

        assert sys.argv == original_argv

    def test_with_argv_empty_list(self):
        """Test that with_argv([]) clears arguments but keeps script name."""
        original_argv = sys.argv[:]

        with with_argv([]):
            # Should have only the script name (no additional args)
            assert sys.argv == [original_argv[0]]
            assert len(sys.argv) == 1

        assert sys.argv == original_argv

    def test_with_argv_restores_on_exception(self):
        """Test that sys.argv is restored even if an exception occurs."""
        original_argv = sys.argv[:]
        test_args = ["--error"]

        with pytest.raises(ValueError):
            with with_argv(test_args):
                assert sys.argv == [original_argv[0]] + test_args
                raise ValueError("Test error")

        # Should still restore original argv
        assert sys.argv == original_argv

    def test_with_argv_nested(self):
        """Test nested with_argv contexts."""
        original_argv = sys.argv[:]
        args1 = ["--level1"]
        args2 = ["--level2", "--debug"]

        with with_argv(args1):
            assert sys.argv == [original_argv[0]] + args1

            with with_argv(args2):
                assert sys.argv == [original_argv[0]] + args2

            # Should restore to level 1
            assert sys.argv == [original_argv[0]] + args1

        # Should restore to original
        assert sys.argv == original_argv

    @patch("sys.argv", ["test_script.py", "existing", "args"])
    def test_with_argv_with_existing_args(self):
        """Test with_argv when sys.argv already has arguments."""
        original_argv = sys.argv[:]
        assert original_argv == ["test_script.py", "existing", "args"]

        test_args = ["--new", "args"]

        with with_argv(test_args):
            # Should replace existing args but keep script name
            assert sys.argv == ["test_script.py", "--new", "args"]

        # Should restore original
        assert sys.argv == original_argv
