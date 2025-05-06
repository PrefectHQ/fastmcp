import fastmcp
from fastmcp.utilities.tests import temporary_settings


class TestTemporarySettings:
    def test_temporary_settings(self):
        with temporary_settings(log_level="DEBUG"):
            assert fastmcp.settings.settings.log_level == "DEBUG"
        assert fastmcp.settings.settings.log_level == "INFO"
