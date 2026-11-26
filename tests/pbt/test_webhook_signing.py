"""fastmcp-events signs and checks webhooks the way Standard Webhooks does.

The reference is `standardwebhooks`, the Standard Webhooks project's own
Python library, which is what most receivers will run:

- a signature is byte-for-byte the reference's, for any key, id, time and body
- a delivery fastmcp signs verifies with the reference, and one the reference
  signs verifies with fastmcp, including among several offered signatures
- changing the body, id or timestamp, or checking with another secret, fails
- a timestamp outside the tolerance fails, inside it passes
- whatever the headers hold, `verify_webhook` fails only with
  `WebhookVerificationError`
"""

import base64
import json
import time
from datetime import UTC, datetime

import pytest
import standardwebhooks
from fastmcp_events.webhooks import TIMESTAMP_TOLERANCE_SECONDS, sign, signed_headers
from hypothesis import assume, given
from hypothesis import strategies as st

from fastmcp_events import WebhookVerificationError, verify_webhook

keys = st.binary(min_size=24, max_size=64)
message_ids = st.text(min_size=1, max_size=40)
bodies = st.binary(max_size=300)
json_bodies = st.dictionaries(st.text(max_size=10), st.integers(), max_size=4).map(
    lambda value: json.dumps(value).encode()
)
# Header values as an HTTP server hands them over: latin-1, so any byte.
header_text = st.one_of(
    st.text(st.characters(max_codepoint=0xFF), max_size=120),
    # Mostly non-ASCII, so signature-shaped junk past the ASCII range is common.
    st.text(st.characters(min_codepoint=0x80, max_codepoint=0xFF), min_size=1),
)


def secret_of(key: bytes) -> str:
    return "whsec_" + base64.b64encode(key).decode()


@given(keys, message_ids, st.integers(0, 2**34), bodies)
def test_signature_matches_the_reference(key, message_id, timestamp, body):
    secret = secret_of(key)
    when = datetime.fromtimestamp(timestamp, UTC)
    reference = standardwebhooks.Webhook(secret).sign(message_id, when, body)
    assert sign(secret, message_id, timestamp, body) == reference


@given(keys, message_ids, json_bodies)
def test_reference_verifies_what_fastmcp_signs(key, message_id, body):
    secret = secret_of(key)
    headers = signed_headers(secret, message_id, "sub_1", body)
    assert standardwebhooks.Webhook(secret).verify(body, headers) == json.loads(body)


@given(keys, keys, message_ids, json_bodies, st.integers(0, 3))
def test_fastmcp_verifies_what_the_reference_signs(
    key, other, message_id, body, position
):
    secret = secret_of(key)
    now = int(time.time())
    when = datetime.fromtimestamp(now, UTC)
    signature = standardwebhooks.Webhook(secret).sign(message_id, when, body)
    # Several signatures, as during key rotation; ours is somewhere among them.
    decoys = [sign(secret_of(other), message_id, now, body + b" ")] * 3
    decoys.insert(position, signature)
    headers = {
        "webhook-id": message_id,
        "webhook-timestamp": str(now),
        "webhook-signature": " ".join(decoys),
    }
    assert verify_webhook(secret, headers, body) == json.loads(body)
    # Rotation on the receiving side: any of the secrets may match.
    assert verify_webhook([secret_of(other), secret], headers, body) == json.loads(body)


@given(
    keys,
    keys,
    message_ids,
    json_bodies,
    st.sampled_from(["body", "id", "timestamp", "secret"]),
    st.data(),
)
def test_any_change_fails(key, other, message_id, body, change, data):
    secret = secret_of(key)
    headers = signed_headers(secret, message_id, "sub_1", body)
    checked_with = secret
    if change == "body":
        tampered = data.draw(json_bodies)
        assume(tampered != body)
        body = tampered
    elif change == "id":
        tampered = data.draw(message_ids)
        assume(tampered != message_id)
        headers["webhook-id"] = tampered
    elif change == "timestamp":
        shift = data.draw(st.integers(-60, 60).filter(bool))
        headers["webhook-timestamp"] = str(int(headers["webhook-timestamp"]) + shift)
    else:
        assume(other != key)
        checked_with = secret_of(other)
    with pytest.raises(WebhookVerificationError):
        verify_webhook(checked_with, headers, body)


@given(keys, message_ids, json_bodies, st.integers(-10_000, 10_000))
def test_tolerance_bounds_the_timestamp(key, message_id, body, offset):
    assume(abs(abs(offset) - TIMESTAMP_TOLERANCE_SECONDS) > 2)
    secret = secret_of(key)
    sent = int(time.time()) + offset
    headers = {
        "webhook-id": message_id,
        "webhook-timestamp": str(sent),
        "webhook-signature": sign(secret, message_id, sent, body),
    }
    if abs(offset) < TIMESTAMP_TOLERANCE_SECONDS:
        assert verify_webhook(secret, headers, body) == json.loads(body)
    else:
        with pytest.raises(WebhookVerificationError):
            verify_webhook(secret, headers, body)


@given(
    keys,
    st.dictionaries(
        st.sampled_from(["webhook-id", "webhook-timestamp", "webhook-signature"]),
        header_text,
    ),
    bodies,
)
def test_hostile_headers_only_fail_verification(key, headers, body):
    with pytest.raises(WebhookVerificationError):
        verify_webhook(secret_of(key), headers, body)


@given(keys, message_ids, header_text, bodies)
def test_hostile_signatures_only_fail_verification(key, message_id, signature, body):
    # A fresh, well-formed delivery whose signature header is anything at all.
    headers = {
        "webhook-id": message_id,
        "webhook-timestamp": str(int(time.time())),
        "webhook-signature": signature,
    }
    with pytest.raises(WebhookVerificationError):
        verify_webhook(secret_of(key), headers, body)
