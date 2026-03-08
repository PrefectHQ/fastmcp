"""Cryptographic contract signing and verification.

Supports multiple signing algorithms:
- HMAC-SHA256: Shared secret, fast, suitable for single-trust-domain
- RSA-PSS: Asymmetric, suitable for cross-organization
- ECDSA (P-256): Compact signatures, suitable for high-volume

All methods produce base64-encoded signatures over the canonical
JSON representation of contract data.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class SigningAlgorithm(Enum):
    """Supported signing algorithms."""

    HMAC_SHA256 = "hmac-sha256"
    RSA_PSS = "rsa-pss"
    ECDSA_P256 = "ecdsa-p256"


@dataclass(frozen=True)
class SignatureInfo:
    """Metadata about a signature.

    Attributes:
        algorithm: The signing algorithm used.
        signer_id: Identity of the signing party.
        signature: Base64-encoded signature bytes.
        key_id: Identifier of the key used (for key rotation).
    """

    algorithm: SigningAlgorithm
    signer_id: str
    signature: str
    key_id: str = ""


def _canonicalize(data: dict[str, Any]) -> bytes:
    """Produce a canonical byte representation for signing.

    Uses sorted JSON keys with no whitespace for deterministic output.
    """
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_digest(data: dict[str, Any]) -> str:
    """Compute SHA-256 digest of canonicalized data.

    Returns hex-encoded hash string.
    """
    canonical = _canonicalize(data)
    return hashlib.sha256(canonical).hexdigest()


class ContractCryptoHandler:
    """Handles cryptographic operations for contract signing.

    Currently implements HMAC-SHA256 natively. RSA and ECDSA require
    the ``cryptography`` package as an optional dependency.

    Example::

        handler = ContractCryptoHandler(
            algorithm=SigningAlgorithm.HMAC_SHA256,
            secret_key=b"my-shared-secret",
        )

        contract_data = contract.to_dict()
        sig = handler.sign(contract_data, signer_id="server-1")
        assert handler.verify(contract_data, sig)
    """

    def __init__(
        self,
        algorithm: SigningAlgorithm = SigningAlgorithm.HMAC_SHA256,
        *,
        secret_key: bytes | None = None,
        private_key: Any = None,
        public_key: Any = None,
        key_id: str = "",
    ) -> None:
        self.algorithm = algorithm
        self._secret_key = secret_key
        self._private_key = private_key
        self._public_key = public_key
        self._key_id = key_id

        if algorithm == SigningAlgorithm.HMAC_SHA256:
            if secret_key is None:
                raise ValueError("HMAC-SHA256 requires a secret_key")
        elif algorithm in (SigningAlgorithm.RSA_PSS, SigningAlgorithm.ECDSA_P256):
            if private_key is None and public_key is None:
                raise ValueError(
                    f"{algorithm.value} requires private_key (for signing) "
                    f"or public_key (for verification)"
                )

    def sign(self, data: dict[str, Any], signer_id: str) -> SignatureInfo:
        """Sign canonicalized contract data.

        Args:
            data: Contract data to sign (e.g., from contract.to_dict()).
            signer_id: Identity of the signing party.

        Returns:
            SignatureInfo with the base64-encoded signature.

        Raises:
            ValueError: If signing key is not configured.
        """
        canonical = _canonicalize(data)

        if self.algorithm == SigningAlgorithm.HMAC_SHA256:
            sig_bytes = hmac.new(
                self._secret_key,  # type: ignore[arg-type]
                canonical,
                hashlib.sha256,
            ).digest()
        elif self.algorithm == SigningAlgorithm.RSA_PSS:
            sig_bytes = self._sign_rsa(canonical)
        elif self.algorithm == SigningAlgorithm.ECDSA_P256:
            sig_bytes = self._sign_ecdsa(canonical)
        else:
            raise ValueError(f"Unsupported algorithm: {self.algorithm}")

        return SignatureInfo(
            algorithm=self.algorithm,
            signer_id=signer_id,
            signature=base64.b64encode(sig_bytes).decode("ascii"),
            key_id=self._key_id,
        )

    def verify(self, data: dict[str, Any], signature: SignatureInfo) -> bool:
        """Verify a signature against contract data.

        Args:
            data: The contract data that was signed.
            signature: The signature to verify.

        Returns:
            True if signature is valid, False otherwise.
        """
        canonical = _canonicalize(data)

        try:
            sig_bytes = base64.b64decode(signature.signature)
        except Exception:
            logger.warning("Failed to decode signature base64")
            return False

        try:
            if signature.algorithm == SigningAlgorithm.HMAC_SHA256:
                expected = hmac.new(
                    self._secret_key,  # type: ignore[arg-type]
                    canonical,
                    hashlib.sha256,
                ).digest()
                return hmac.compare_digest(sig_bytes, expected)
            elif signature.algorithm == SigningAlgorithm.RSA_PSS:
                return self._verify_rsa(canonical, sig_bytes)
            elif signature.algorithm == SigningAlgorithm.ECDSA_P256:
                return self._verify_ecdsa(canonical, sig_bytes)
            else:
                logger.warning("Unknown algorithm: %s", signature.algorithm)
                return False
        except Exception:
            logger.warning("Signature verification failed", exc_info=True)
            return False

    def _sign_rsa(self, data: bytes) -> bytes:
        """Sign with RSA-PSS (requires cryptography package)."""
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError:
            raise ImportError(
                "RSA signing requires the 'cryptography' package. "
                "Install with: pip install cryptography"
            ) from None

        if self._private_key is None:
            raise ValueError("RSA signing requires a private_key")

        return self._private_key.sign(
            data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

    def _verify_rsa(self, data: bytes, signature: bytes) -> bool:
        """Verify RSA-PSS signature."""
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError:
            raise ImportError(
                "RSA verification requires the 'cryptography' package."
            ) from None

        key = self._public_key or self._private_key.public_key()

        try:
            key.verify(
                signature,
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            return True
        except Exception:
            return False

    def _sign_ecdsa(self, data: bytes) -> bytes:
        """Sign with ECDSA P-256 (requires cryptography package)."""
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            raise ImportError(
                "ECDSA signing requires the 'cryptography' package."
            ) from None

        if self._private_key is None:
            raise ValueError("ECDSA signing requires a private_key")

        return self._private_key.sign(data, ec.ECDSA(hashes.SHA256()))

    def _verify_ecdsa(self, data: bytes, signature: bytes) -> bool:
        """Verify ECDSA P-256 signature."""
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            raise ImportError(
                "ECDSA verification requires the 'cryptography' package."
            ) from None

        key = self._public_key or self._private_key.public_key()

        try:
            key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
            return True
        except Exception:
            return False
