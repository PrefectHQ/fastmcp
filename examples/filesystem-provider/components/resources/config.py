"""Configuration resources - static and templated."""

import json

from fastmcp.resources import resource


# Static resource - no parameters in URI
@resource("config://app")
def get_app_config() -> str:
    """Get application configuration."""
    return json.dumps(
        {
            "name": "FilesystemDemo",
            "version": "1.0.0",
            "features": ["tools", "resources", "prompts"],
        },
        indent=2,
    )


# Resource template - {env} is a parameter
@resource("config://env/{env}")
def get_env_config(env: str) -> str:
    """Get environment-specific configuration.

    Args:
        env: Environment name (dev, staging, prod).
    """
    configs = {
        "dev": {"debug": True, "log_level": "DEBUG", "database": "localhost"},
        "staging": {"debug": True, "log_level": "INFO", "database": "staging-db"},
        "prod": {"debug": False, "log_level": "WARNING", "database": "prod-db"},
    }
    config = configs.get(env, {"error": f"Unknown environment: {env}"})
    return json.dumps(config, indent=2)


# Resource with custom metadata
@resource(
    "config://features",
    name="feature-flags",
    mime_type="application/json",
    tags={"config", "features"},
)
def get_feature_flags() -> str:
    """Get feature flags configuration."""
    return json.dumps(
        {
            "dark_mode": True,
            "beta_features": False,
            "max_upload_size_mb": 100,
        },
        indent=2,
    )
