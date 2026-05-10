"""Backward compatibility shim for AWS Cognito auth provider."""

from __future__ import annotations

import warnings

from fastmcp import settings
from fastmcp.exceptions import FastMCPDeprecationWarning

if settings.deprecation_warnings:
    warnings.warn(
        "fastmcp.server.auth.providers.aws is deprecated. "
        "Import from fastmcp.server.plugins.auth.aws.provider instead.",
        FastMCPDeprecationWarning,
        stacklevel=2,
    )

from fastmcp.server.plugins.auth.aws.provider import (
    AWSCognitoProvider,
    AWSCognitoTokenVerifier,
)

__all__ = ["AWSCognitoProvider", "AWSCognitoTokenVerifier"]
