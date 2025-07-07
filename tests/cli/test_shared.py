from fastmcp.cli.cli import _parse_env_var


class TestEnvVarParsing:
    """Test environment variable parsing functionality."""

    def test_parse_env_var_simple(self):
        """Test parsing simple environment variable."""
        key, value = _parse_env_var("API_KEY=secret123")
        assert key == "API_KEY"
        assert value == "secret123"

    def test_parse_env_var_with_equals_in_value(self):
        """Test parsing env var with equals signs in the value."""
        key, value = _parse_env_var("DATABASE_URL=postgresql://user:pass@host:5432/db")
        assert key == "DATABASE_URL"
        assert value == "postgresql://user:pass@host:5432/db"

    def test_parse_env_var_with_spaces(self):
        """Test parsing env var with spaces (should be stripped)."""
        key, value = _parse_env_var("  API_KEY  =  secret with spaces  ")
        assert key == "API_KEY"
        assert value == "secret with spaces"

    def test_parse_env_var_empty_value(self):
        """Test parsing env var with empty value."""
        key, value = _parse_env_var("EMPTY_VAR=")
        assert key == "EMPTY_VAR"
        assert value == ""
