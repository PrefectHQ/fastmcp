import fastmcp
from fastmcp.utilities.tests import temporary_settings


class TestTemporarySettings:
    def test_temporary_settings(self):
        assert fastmcp.settings.log_level == "DEBUG"
        with temporary_settings(log_level="ERROR"):
            assert fastmcp.settings.log_level == "ERROR"
        assert fastmcp.settings.log_level == "DEBUG"
