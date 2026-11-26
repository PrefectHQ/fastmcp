"""Signing and checking webhook deliveries, as Standard Webhooks defines them.

Experimental: this API may change in any release without a deprecation period.

SEP-3415 webhook delivery is a profile of Standard Webhooks: every POST carries
`webhook-id`, `webhook-timestamp` and `webhook-signature` headers, signed with
the `whsec_` secret the client chose when it subscribed. A receiver checks the
signature with `verify_webhook` before it trusts anything in the body.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Iterable, Mapping
from typing import Any

SECRET_PREFIX = "whsec_"
SUBSCRIPTION_ID_HEADER = "X-MCP-Subscription-Id"
#: Deliveries older than this are refused, as Standard Webhooks recommends.
TIMESTAMP_TOLERANCE_SECONDS = 300


class WebhookVerificationError(ValueError):
    """A delivery's signature or timestamp did not check out."""


def new_webhook_secret() -> str:
    """A fresh signing secret: `whsec_` and 32 random bytes in base64."""
    return SECRET_PREFIX + base64.b64encode(secrets.token_bytes(32)).decode()


def secret_bytes(secret: str) -> bytes:
    """The key a `whsec_` secret encodes.

    Raises:
        ValueError: If `secret` is not `whsec_` and base64 of 24 to 64 bytes.
    """
    if not secret.startswith(SECRET_PREFIX):
        raise ValueError("A webhook secret starts with whsec_")
    try:
        key = base64.b64decode(secret.removeprefix(SECRET_PREFIX), validate=True)
    except binascii.Error:
        raise ValueError("A webhook secret is whsec_ followed by base64") from None
    if not 24 <= len(key) <= 64:
        raise ValueError("A webhook secret encodes 24 to 64 bytes")
    return key


def sign(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    """The `v1,` signature of one delivery."""
    signed = f"{message_id}.{timestamp}.".encode() + body
    digest = hmac.new(secret_bytes(secret), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def signed_headers(
    secret: str, message_id: str, subscription_id: str, body: bytes
) -> dict[str, str]:
    """The headers one delivery carries, signed now."""
    timestamp = int(time.time())
    return {
        "content-type": "application/json",
        "webhook-id": message_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": sign(secret, message_id, timestamp, body),
        SUBSCRIPTION_ID_HEADER: subscription_id,
    }


def verify_webhook(
    secrets: str | Iterable[str],
    headers: Mapping[str, str],
    body: bytes,
    *,
    tolerance: int = TIMESTAMP_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    """Check a delivery's signature and age, then parse its body.

    Pass the raw body exactly as received, before any parsing. `secrets` is
    the subscription's secret, or several while one is being rotated. A body
    with a `type` field is a control message (`gap`, `terminated` or
    `verification`); any other body is one event.

    Raises:
        WebhookVerificationError: If the headers are missing, the timestamp
            is outside `tolerance` seconds, or no signature matches.
    """
    lowered = {key.lower(): value for key, value in headers.items()}
    message_id = lowered.get("webhook-id")
    timestamp = lowered.get("webhook-timestamp")
    signatures = lowered.get("webhook-signature")
    if not (message_id and timestamp and signatures):
        raise WebhookVerificationError("Missing Standard Webhooks headers")
    try:
        sent = int(timestamp)
    except ValueError:
        raise WebhookVerificationError("Invalid webhook-timestamp") from None
    if abs(time.time() - sent) > tolerance:
        raise WebhookVerificationError("Delivery timestamp is outside the tolerance")
    offered = signatures.split()
    candidates = [secrets] if isinstance(secrets, str) else list(secrets)
    for secret in candidates:
        expected = sign(secret, message_id, sent, body)
        if any(hmac.compare_digest(expected, given) for given in offered):
            return json.loads(body)
    raise WebhookVerificationError("No signature matches")
