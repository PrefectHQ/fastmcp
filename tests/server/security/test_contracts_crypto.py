"""Tests for contract crypto handler."""

from __future__ import annotations

import pytest

from fastmcp.server.security.contracts.crypto import (
    ContractCryptoHandler,
    SignatureInfo,
    SigningAlgorithm,
    compute_digest,
)


class TestCanonicalizeAndDigest:
    def test_digest_deterministic(self):
        data = {"b": 2, "a": 1}
        assert compute_digest(data) == compute_digest({"a": 1, "b": 2})

    def test_digest_different_for_different_data(self):
        assert compute_digest({"a": 1}) != compute_digest({"a": 2})


class TestHMACSigning:
    def test_hmac_requires_secret(self):
        with pytest.raises(ValueError, match="secret_key"):
            ContractCryptoHandler(algorithm=SigningAlgorithm.HMAC_SHA256)

    def test_sign_and_verify(self):
        handler = ContractCryptoHandler(
            algorithm=SigningAlgorithm.HMAC_SHA256,
            secret_key=b"test-secret-key",
        )
        data = {"contract_id": "c1", "terms": []}
        sig = handler.sign(data, signer_id="server-1")

        assert isinstance(sig, SignatureInfo)
        assert sig.algorithm == SigningAlgorithm.HMAC_SHA256
        assert sig.signer_id == "server-1"
        assert sig.signature  # Non-empty

        assert handler.verify(data, sig) is True

    def test_verify_fails_with_wrong_data(self):
        handler = ContractCryptoHandler(
            algorithm=SigningAlgorithm.HMAC_SHA256,
            secret_key=b"test-secret-key",
        )
        data = {"contract_id": "c1"}
        sig = handler.sign(data, signer_id="s1")

        # Tamper with data
        tampered = {"contract_id": "c2"}
        assert handler.verify(tampered, sig) is False

    def test_verify_fails_with_wrong_key(self):
        handler1 = ContractCryptoHandler(
            algorithm=SigningAlgorithm.HMAC_SHA256,
            secret_key=b"key-1",
        )
        handler2 = ContractCryptoHandler(
            algorithm=SigningAlgorithm.HMAC_SHA256,
            secret_key=b"key-2",
        )
        data = {"x": 1}
        sig = handler1.sign(data, signer_id="s1")
        assert handler2.verify(data, sig) is False

    def test_verify_bad_base64(self):
        handler = ContractCryptoHandler(
            algorithm=SigningAlgorithm.HMAC_SHA256,
            secret_key=b"key",
        )
        bad_sig = SignatureInfo(
            algorithm=SigningAlgorithm.HMAC_SHA256,
            signer_id="s1",
            signature="not-valid-base64!!!",
        )
        assert handler.verify({"x": 1}, bad_sig) is False

    def test_key_id_stored(self):
        handler = ContractCryptoHandler(
            algorithm=SigningAlgorithm.HMAC_SHA256,
            secret_key=b"key",
            key_id="key-v1",
        )
        sig = handler.sign({"x": 1}, signer_id="s1")
        assert sig.key_id == "key-v1"


class TestRSARequirements:
    def test_rsa_requires_key(self):
        with pytest.raises(ValueError, match="private_key"):
            ContractCryptoHandler(algorithm=SigningAlgorithm.RSA_PSS)


class TestECDSARequirements:
    def test_ecdsa_requires_key(self):
        with pytest.raises(ValueError, match="private_key"):
            ContractCryptoHandler(algorithm=SigningAlgorithm.ECDSA_P256)
