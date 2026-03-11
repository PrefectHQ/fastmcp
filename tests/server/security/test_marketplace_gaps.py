"""Tests for marketplace gap closures: persistence, versioning, moderation, signature verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

from fastmcp.server.security.certification.attestation import (
    AttestationStatus,
    CertificationLevel,
    ToolAttestation,
)
from fastmcp.server.security.certification.manifest import SecurityManifest
from fastmcp.server.security.gateway.tool_marketplace import (
    ModerationAction,
    ModerationDecision,
    PublishStatus,
    ReviewRating,
    ToolCategory,
    ToolMarketplace,
    ToolVersion,
    compute_manifest_digest,
    verify_attestation_signature,
)

# ── Helpers ──────────────────────────────────────────────────


def _make_manifest(**overrides: Any) -> SecurityManifest:
    """Create a test manifest."""
    defaults = {
        "manifest_id": "test-manifest",
        "tool_name": "test-tool",
        "version": "1.0.0",
        "author": "tester",
    }
    defaults.update(overrides)
    return SecurityManifest(**defaults)


def _make_attestation(
    *,
    valid: bool = True,
    manifest_digest: str = "",
    signature: str = "sig-abc",
    **overrides: Any,
) -> ToolAttestation:
    """Create a test attestation."""
    att = ToolAttestation(
        tool_name="test-tool",
        tool_version="1.0.0",
        author="tester",
        certification_level=CertificationLevel.BASIC,
        status=AttestationStatus.VALID if valid else AttestationStatus.EXPIRED,
        signature=signature,
        manifest_digest=manifest_digest,
        **overrides,
    )
    if valid and att.expires_at is None:
        att.expires_at = datetime.now(timezone.utc) + timedelta(days=90)
    return att


def _make_marketplace(**kwargs: Any) -> ToolMarketplace:
    """Create a test marketplace."""
    return ToolMarketplace(**kwargs)


# ══════════════════════════════════════════════════════════════
# Tool Versioning Tests
# ══════════════════════════════════════════════════════════════


class TestToolVersioning:
    """Tests for tool version history."""

    def test_initial_publish_records_version(self) -> None:
        mp = _make_marketplace()
        listing = mp.publish("t1", version="1.0.0")
        assert len(listing.version_history) == 1
        assert listing.version_history[0].version == "1.0.0"
        assert listing.version_history[0].changelog == "Initial release"

    def test_update_records_new_version(self) -> None:
        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        mp.publish("t1", version="2.0.0", changelog="Major update")
        listing = mp.get_by_name("t1")
        assert listing is not None
        assert len(listing.version_history) == 2
        assert listing.version_history[1].version == "2.0.0"
        assert listing.version_history[1].changelog == "Major update"

    def test_same_version_no_duplicate(self) -> None:
        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        mp.publish("t1", version="1.0.0")  # Same version
        listing = mp.get_by_name("t1")
        assert listing is not None
        assert len(listing.version_history) == 1

    def test_version_with_manifest_digest(self) -> None:
        mp = _make_marketplace()
        manifest = _make_manifest()
        mp.publish("t1", version="1.0.0", manifest=manifest)
        listing = mp.get_by_name("t1")
        assert listing is not None
        assert listing.version_history[0].manifest_digest != ""

    def test_version_with_attestation_id(self) -> None:
        mp = _make_marketplace()
        att = _make_attestation()
        mp.publish("t1", version="1.0.0", attestation=att)
        listing = mp.get_by_name("t1")
        assert listing is not None
        assert listing.version_history[0].attestation_id == att.attestation_id

    def test_get_version_history(self) -> None:
        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        mp.publish("t1", version="2.0.0")
        mp.publish("t1", version="3.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        history = mp.get_version_history(listing.listing_id)
        assert len(history) == 3

    def test_get_version_by_string(self) -> None:
        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        v = mp.get_version(listing.listing_id, "1.0.0")
        assert v is not None
        assert v.version == "1.0.0"

    def test_get_version_nonexistent(self) -> None:
        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        assert mp.get_version(listing.listing_id, "9.9.9") is None

    def test_yank_version(self) -> None:
        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        assert mp.yank_version(listing.listing_id, "1.0.0", reason="Security issue")
        v = mp.get_version(listing.listing_id, "1.0.0")
        assert v is not None
        assert v.yanked is True
        assert v.yank_reason == "Security issue"

    def test_unyank_version(self) -> None:
        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        mp.yank_version(listing.listing_id, "1.0.0")
        assert mp.unyank_version(listing.listing_id, "1.0.0")
        v = mp.get_version(listing.listing_id, "1.0.0")
        assert v is not None
        assert v.yanked is False

    def test_yank_nonexistent_version(self) -> None:
        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        assert not mp.yank_version(listing.listing_id, "9.9.9")

    def test_available_versions_excludes_yanked(self) -> None:
        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        mp.publish("t1", version="2.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        assert listing.available_versions == ["1.0.0", "2.0.0"]
        mp.yank_version(listing.listing_id, "1.0.0")
        assert listing.available_versions == ["2.0.0"]

    def test_latest_version_skips_yanked(self) -> None:
        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        mp.publish("t1", version="2.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        mp.yank_version(listing.listing_id, "2.0.0")
        assert listing.latest_version is not None
        assert listing.latest_version.version == "1.0.0"

    def test_install_yanked_version_rejected(self) -> None:
        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        mp.publish("t1", version="2.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        mp.yank_version(listing.listing_id, "1.0.0")
        record = mp.install(listing.listing_id, version="1.0.0")
        assert record is None

    def test_to_dict_includes_versions(self) -> None:
        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        mp.publish("t1", version="2.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        d = listing.to_dict()
        assert d["version_count"] == 2
        assert d["available_versions"] == ["1.0.0", "2.0.0"]

    def test_tool_version_to_dict(self) -> None:
        tv = ToolVersion(version="1.0.0", changelog="Initial")
        d = tv.to_dict()
        assert d["version"] == "1.0.0"
        assert d["changelog"] == "Initial"
        assert d["yanked"] is False


# ══════════════════════════════════════════════════════════════
# Moderation Workflow Tests
# ══════════════════════════════════════════════════════════════


class TestModeration:
    """Tests for the moderation workflow."""

    def test_require_moderation_sends_to_pending(self) -> None:
        mp = _make_marketplace(require_moderation=True)
        listing = mp.publish("t1", version="1.0.0")
        assert listing.status == PublishStatus.PENDING_REVIEW

    def test_no_moderation_publishes_directly(self) -> None:
        mp = _make_marketplace(require_moderation=False)
        listing = mp.publish("t1", version="1.0.0")
        assert listing.status == PublishStatus.PUBLISHED

    def test_get_pending_review(self) -> None:
        mp = _make_marketplace(require_moderation=True)
        mp.publish("t1", version="1.0.0")
        mp.publish("t2", version="1.0.0")
        pending = mp.get_pending_review()
        assert len(pending) == 2

    def test_approve_listing(self) -> None:
        mp = _make_marketplace(require_moderation=True)
        listing = mp.publish("t1", version="1.0.0")
        decision = mp.moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action=ModerationAction.APPROVE,
            reason="Looks good",
        )
        assert decision is not None
        assert listing.status == PublishStatus.PUBLISHED
        assert len(listing.moderation_log) == 1

    def test_reject_listing(self) -> None:
        mp = _make_marketplace(require_moderation=True)
        listing = mp.publish("t1", version="1.0.0")
        decision = mp.moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action=ModerationAction.REJECT,
            reason="Missing docs",
        )
        assert decision is not None
        assert listing.status == PublishStatus.REJECTED

    def test_suspend_listing(self) -> None:
        mp = _make_marketplace()
        listing = mp.publish("t1", version="1.0.0")
        mp.moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action=ModerationAction.SUSPEND,
            reason="Violation",
        )
        assert listing.status == PublishStatus.SUSPENDED

    def test_unsuspend_listing(self) -> None:
        mp = _make_marketplace()
        listing = mp.publish("t1", version="1.0.0")
        mp.moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action=ModerationAction.SUSPEND,
        )
        mp.moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action=ModerationAction.UNSUSPEND,
        )
        assert listing.status == PublishStatus.PUBLISHED

    def test_deprecate_listing(self) -> None:
        mp = _make_marketplace()
        listing = mp.publish("t1", version="1.0.0")
        mp.moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action=ModerationAction.DEPRECATE,
            reason="Replaced by t2",
        )
        assert listing.status == PublishStatus.DEPRECATED

    def test_request_changes(self) -> None:
        mp = _make_marketplace(require_moderation=True)
        listing = mp.publish("t1", version="1.0.0")
        mp.moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action=ModerationAction.REQUEST_CHANGES,
            reason="Improve description",
        )
        assert listing.status == PublishStatus.DRAFT

    def test_moderate_nonexistent(self) -> None:
        mp = _make_marketplace()
        assert (
            mp.moderate(
                "nonexistent",
                moderator_id="mod-1",
                action=ModerationAction.APPROVE,
            )
            is None
        )

    def test_moderation_log(self) -> None:
        mp = _make_marketplace(require_moderation=True)
        listing = mp.publish("t1", version="1.0.0")
        mp.moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action=ModerationAction.REQUEST_CHANGES,
            reason="Fix issues",
        )
        mp.moderate(
            listing.listing_id,
            moderator_id="mod-2",
            action=ModerationAction.APPROVE,
            reason="Issues resolved",
        )
        log = mp.get_moderation_log(listing.listing_id)
        assert len(log) == 2
        assert log[0].action == ModerationAction.REQUEST_CHANGES
        assert log[1].action == ModerationAction.APPROVE

    def test_moderation_decision_to_dict(self) -> None:
        d = ModerationDecision(
            listing_id="l-1",
            moderator_id="mod-1",
            action=ModerationAction.APPROVE,
            reason="OK",
        )
        serialized = d.to_dict()
        assert serialized["action"] == "approve"
        assert serialized["reason"] == "OK"

    def test_require_moderation_property(self) -> None:
        mp1 = _make_marketplace(require_moderation=True)
        mp2 = _make_marketplace(require_moderation=False)
        assert mp1.require_moderation is True
        assert mp2.require_moderation is False

    def test_pending_review_in_statistics(self) -> None:
        mp = _make_marketplace(require_moderation=True)
        mp.publish("t1", version="1.0.0")
        stats = mp.get_statistics()
        assert stats["pending_review"] == 1


# ══════════════════════════════════════════════════════════════
# Signature Verification Tests
# ══════════════════════════════════════════════════════════════


class TestSignatureVerification:
    """Tests for package signature verification on install."""

    def test_compute_manifest_digest(self) -> None:
        manifest = _make_manifest()
        digest = compute_manifest_digest(manifest)
        assert isinstance(digest, str)
        assert len(digest) == 64  # SHA-256 hex

    def test_verify_valid_attestation(self) -> None:
        att = _make_attestation()
        passed, reason = verify_attestation_signature(att)
        assert passed is True

    def test_verify_expired_attestation(self) -> None:
        att = _make_attestation(valid=False)
        passed, reason = verify_attestation_signature(att)
        assert passed is False
        assert "expired" in reason.lower() or "not valid" in reason.lower()

    def test_verify_no_signature(self) -> None:
        att = _make_attestation(signature="")
        passed, reason = verify_attestation_signature(att)
        assert passed is False
        assert "no signature" in reason.lower()

    def test_verify_manifest_digest_match(self) -> None:
        manifest = _make_manifest()
        digest = compute_manifest_digest(manifest)
        att = _make_attestation(manifest_digest=digest)
        passed, reason = verify_attestation_signature(att, manifest)
        assert passed is True

    def test_verify_manifest_digest_mismatch(self) -> None:
        manifest = _make_manifest()
        att = _make_attestation(manifest_digest="wrong-digest")
        passed, reason = verify_attestation_signature(att, manifest)
        assert passed is False
        assert "mismatch" in reason.lower()

    def test_install_with_verification_success(self) -> None:
        mp = _make_marketplace()
        att = _make_attestation()
        listing = mp.publish("t1", version="1.0.0", attestation=att)
        record = mp.install(listing.listing_id, verify_signature=True)
        assert record is not None
        assert record.signature_verified is True

    def test_install_with_verification_no_attestation(self) -> None:
        mp = _make_marketplace()
        listing = mp.publish("t1", version="1.0.0")
        record = mp.install(listing.listing_id, verify_signature=True)
        assert record is None

    def test_install_with_verification_expired_attestation(self) -> None:
        mp = _make_marketplace()
        att = _make_attestation(valid=False)
        listing = mp.publish("t1", version="1.0.0", attestation=att)
        record = mp.install(listing.listing_id, verify_signature=True)
        assert record is None

    def test_install_without_verification(self) -> None:
        mp = _make_marketplace()
        listing = mp.publish("t1", version="1.0.0")
        record = mp.install(listing.listing_id, verify_signature=False)
        assert record is not None
        assert record.signature_verified is False

    def test_install_with_manifest_digest_verification(self) -> None:
        mp = _make_marketplace()
        manifest = _make_manifest()
        digest = compute_manifest_digest(manifest)
        att = _make_attestation(manifest_digest=digest)
        listing = mp.publish("t1", version="1.0.0", manifest=manifest, attestation=att)
        record = mp.install(listing.listing_id, verify_signature=True)
        assert record is not None
        assert record.signature_verified is True


# ══════════════════════════════════════════════════════════════
# Persistence Tests
# ══════════════════════════════════════════════════════════════


class TestPersistence:
    """Tests for storage backend integration."""

    def _make_mock_backend(self) -> MagicMock:
        backend = MagicMock()
        backend.load_tool_marketplace.return_value = {"listings": {}}
        return backend

    def test_publish_persists_listing(self) -> None:
        backend = self._make_mock_backend()
        mp = _make_marketplace(backend=backend)
        listing = mp.publish("t1", version="1.0.0")
        backend.save_tool_listing.assert_called()
        args = backend.save_tool_listing.call_args
        assert args[0][1] == listing.listing_id

    def test_unpublish_removes_from_backend(self) -> None:
        backend = self._make_mock_backend()
        mp = _make_marketplace(backend=backend)
        listing = mp.publish("t1", version="1.0.0")
        mp.unpublish(listing.listing_id)
        backend.remove_tool_listing.assert_called_with("default", listing.listing_id)

    def test_review_persists(self) -> None:
        backend = self._make_mock_backend()
        mp = _make_marketplace(backend=backend)
        listing = mp.publish("t1", version="1.0.0")
        mp.add_review(listing.listing_id, reviewer_id="r1", rating=ReviewRating.FIVE)
        backend.append_tool_review.assert_called()

    def test_install_persists(self) -> None:
        backend = self._make_mock_backend()
        mp = _make_marketplace(backend=backend)
        listing = mp.publish("t1", version="1.0.0")
        mp.install(listing.listing_id, installer_id="u1")
        backend.append_tool_install.assert_called()

    def test_load_from_backend(self) -> None:
        backend = MagicMock()
        backend.load_tool_marketplace.return_value = {
            "listings": {
                "listing-1": {
                    "listing_id": "listing-1",
                    "tool_name": "loaded-tool",
                    "display_name": "Loaded Tool",
                    "version": "1.0.0",
                    "author": "tester",
                    "status": "published",
                    "categories": ["search"],
                    "tags": ["fast"],
                }
            }
        }
        mp = _make_marketplace(backend=backend)
        assert mp.listing_count == 1
        listing = mp.get_by_name("loaded-tool")
        assert listing is not None
        assert listing.display_name == "Loaded Tool"
        assert ToolCategory.SEARCH in listing.categories

    def test_backend_load_failure_handled(self) -> None:
        backend = MagicMock()
        backend.load_tool_marketplace.side_effect = RuntimeError("DB down")
        mp = _make_marketplace(backend=backend)
        assert mp.listing_count == 0  # Graceful degradation

    def test_yank_persists(self) -> None:
        backend = self._make_mock_backend()
        mp = _make_marketplace(backend=backend)
        listing = mp.publish("t1", version="1.0.0")
        backend.save_tool_listing.reset_mock()
        mp.yank_version(listing.listing_id, "1.0.0")
        backend.save_tool_listing.assert_called()

    def test_moderate_persists(self) -> None:
        backend = self._make_mock_backend()
        mp = _make_marketplace(backend=backend, require_moderation=True)
        listing = mp.publish("t1", version="1.0.0")
        backend.save_tool_listing.reset_mock()
        mp.moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action=ModerationAction.APPROVE,
        )
        backend.save_tool_listing.assert_called()


# ══════════════════════════════════════════════════════════════
# Bridge Enhancement Tests
# ══════════════════════════════════════════════════════════════


class TestBridgeEnhancements:
    """Tests for the updated MarketplaceDataBridge."""

    def test_export_includes_moderation_queue(self) -> None:
        from fastmcp.server.security.gateway.marketplace_bridge import (
            MarketplaceDataBridge,
        )

        mp = _make_marketplace(require_moderation=True)
        mp.publish("t1", version="1.0.0")
        bridge = MarketplaceDataBridge(marketplace=mp)
        data = bridge.export()
        assert "moderation_queue" in data
        assert len(data["moderation_queue"]) == 1

    def test_stats_include_pending(self) -> None:
        from fastmcp.server.security.gateway.marketplace_bridge import (
            MarketplaceDataBridge,
        )

        mp = _make_marketplace(require_moderation=True)
        mp.publish("t1", version="1.0.0")
        bridge = MarketplaceDataBridge(marketplace=mp)
        stats = bridge.build_stats()
        assert stats["pending_review"] == 1

    def test_listing_card_includes_versions(self) -> None:
        from fastmcp.server.security.gateway.marketplace_bridge import (
            MarketplaceDataBridge,
        )

        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        mp.publish("t1", version="2.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        bridge = MarketplaceDataBridge(marketplace=mp)
        card = bridge._listing_to_card(listing)
        assert card["version_count"] == 2
        assert card["available_versions"] == ["1.0.0", "2.0.0"]

    def test_build_version_history(self) -> None:
        from fastmcp.server.security.gateway.marketplace_bridge import (
            MarketplaceDataBridge,
        )

        mp = _make_marketplace()
        mp.publish("t1", version="1.0.0")
        mp.publish("t1", version="2.0.0", changelog="Big update")
        listing = mp.get_by_name("t1")
        assert listing is not None
        bridge = MarketplaceDataBridge(marketplace=mp)
        versions = bridge.build_version_history(listing.listing_id)
        assert len(versions) == 2
        assert versions[0]["version"] == "2.0.0"  # newest first
        assert versions[0]["changelog"] == "Big update"

    def test_build_moderation_log(self) -> None:
        from fastmcp.server.security.gateway.marketplace_bridge import (
            MarketplaceDataBridge,
        )

        mp = _make_marketplace(require_moderation=True)
        listing = mp.publish("t1", version="1.0.0")
        mp.moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action=ModerationAction.APPROVE,
            reason="OK",
        )
        bridge = MarketplaceDataBridge(marketplace=mp)
        log = bridge.build_moderation_log(listing.listing_id)
        assert len(log) == 1
        assert log[0]["action"] == "approve"

    def test_detail_includes_new_sections(self) -> None:
        from fastmcp.server.security.gateway.marketplace_bridge import (
            MarketplaceDataBridge,
        )

        mp = _make_marketplace()
        listing = mp.publish("t1", version="1.0.0")
        bridge = MarketplaceDataBridge(marketplace=mp)
        detail = bridge.build_listing_detail(listing.listing_id)
        assert detail is not None
        assert "version_history" in detail
        assert "moderation_log" in detail


# ══════════════════════════════════════════════════════════════
# API Tests
# ══════════════════════════════════════════════════════════════


class TestMarketplaceAPI:
    """Tests for new marketplace API endpoints."""

    def _make_api(self, **kwargs: Any) -> Any:
        from fastmcp.server.security.http.api import SecurityAPI

        mp = _make_marketplace(**kwargs)
        return SecurityAPI(marketplace=mp), mp

    def test_install_endpoint(self) -> None:
        api, mp = self._make_api()
        listing = mp.publish("t1", version="1.0.0")
        result = api.marketplace_install(listing.listing_id, installer_id="u1")
        assert "install_id" in result
        assert result["version"] == "1.0.0"

    def test_install_nonexistent(self) -> None:
        api, mp = self._make_api()
        result = api.marketplace_install("nonexistent")
        assert result.get("status") == 400

    def test_install_with_verification(self) -> None:
        api, mp = self._make_api()
        att = _make_attestation()
        listing = mp.publish("t1", version="1.0.0", attestation=att)
        result = api.marketplace_install(listing.listing_id, verify_signature=True)
        assert result["signature_verified"] is True

    def test_uninstall_endpoint(self) -> None:
        api, mp = self._make_api()
        listing = mp.publish("t1", version="1.0.0")
        mp.install(listing.listing_id, installer_id="u1")
        result = api.marketplace_uninstall(listing.listing_id, installer_id="u1")
        assert result["success"] is True

    def test_moderate_endpoint(self) -> None:
        api, mp = self._make_api(require_moderation=True)
        listing = mp.publish("t1", version="1.0.0")
        result = api.marketplace_moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action="approve",
            reason="Good",
        )
        assert result["action"] == "approve"

    def test_moderate_invalid_action(self) -> None:
        api, mp = self._make_api()
        listing = mp.publish("t1", version="1.0.0")
        result = api.marketplace_moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action="invalid_action",
        )
        assert result.get("status") == 400

    def test_moderation_queue_endpoint(self) -> None:
        api, mp = self._make_api(require_moderation=True)
        mp.publish("t1", version="1.0.0")
        mp.publish("t2", version="1.0.0")
        result = api.marketplace_moderation_queue()
        assert len(result["queue"]) == 2

    def test_version_history_endpoint(self) -> None:
        api, mp = self._make_api()
        mp.publish("t1", version="1.0.0")
        mp.publish("t1", version="2.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        result = api.marketplace_version_history(listing.listing_id)
        assert len(result["versions"]) == 2

    def test_yank_version_endpoint(self) -> None:
        api, mp = self._make_api()
        mp.publish("t1", version="1.0.0")
        listing = mp.get_by_name("t1")
        assert listing is not None
        result = api.marketplace_yank_version(listing.listing_id, "1.0.0", reason="Bug")
        assert result["success"] is True

    def test_marketplace_not_configured(self) -> None:
        from fastmcp.server.security.http.api import SecurityAPI

        api = SecurityAPI()
        assert api.marketplace_install("x").get("status") == 503
        assert api.marketplace_uninstall("x").get("status") == 503
        assert (
            api.marketplace_moderate("x", moderator_id="m", action="approve").get(
                "status"
            )
            == 503
        )
        assert api.marketplace_moderation_queue().get("status") == 503
        assert api.marketplace_version_history("x").get("status") == 503
        assert api.marketplace_yank_version("x", "1.0").get("status") == 503


# ══════════════════════════════════════════════════════════════
# Event Bus Integration
# ══════════════════════════════════════════════════════════════


class TestEventBusIntegration:
    """Tests for event emission on new operations."""

    def test_install_emits_event(self) -> None:
        from fastmcp.server.security.alerts.bus import SecurityEventBus

        bus = SecurityEventBus()
        events: list[Any] = []
        bus.subscribe(lambda e: events.append(e))
        mp = _make_marketplace(event_bus=bus)
        listing = mp.publish("t1", version="1.0.0")
        events.clear()
        mp.install(listing.listing_id, installer_id="u1")
        assert any("INSTALLED" in e.data.get("action", "") for e in events)

    def test_moderation_emits_event(self) -> None:
        from fastmcp.server.security.alerts.bus import SecurityEventBus

        bus = SecurityEventBus()
        events: list[Any] = []
        bus.subscribe(lambda e: events.append(e))
        mp = _make_marketplace(event_bus=bus, require_moderation=True)
        listing = mp.publish("t1", version="1.0.0")
        events.clear()
        mp.moderate(
            listing.listing_id,
            moderator_id="mod-1",
            action=ModerationAction.APPROVE,
        )
        assert any("MODERATED" in e.data.get("action", "") for e in events)

    def test_install_rejected_emits_event(self) -> None:
        from fastmcp.server.security.alerts.bus import SecurityEventBus

        bus = SecurityEventBus()
        events: list[Any] = []
        bus.subscribe(lambda e: events.append(e))
        mp = _make_marketplace(event_bus=bus)
        listing = mp.publish("t1", version="1.0.0")
        events.clear()
        # No attestation, verify_signature=True → rejected
        mp.install(listing.listing_id, verify_signature=True)
        assert any("REJECTED" in e.data.get("action", "") for e in events)


# ══════════════════════════════════════════════════════════════
# Import Tests
# ══════════════════════════════════════════════════════════════


class TestImports:
    """Verify all new types are importable."""

    def test_new_types_importable(self) -> None:
        from fastmcp.server.security.gateway.tool_marketplace import (
            ModerationAction,
            ModerationDecision,
            ToolVersion,
            compute_manifest_digest,
            verify_attestation_signature,
        )

        assert ModerationAction is not None
        assert ModerationDecision is not None
        assert ToolVersion is not None
        assert compute_manifest_digest is not None
        assert verify_attestation_signature is not None
