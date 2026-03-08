"""Tests for Merkle tree implementation."""

from __future__ import annotations

import pytest

from fastmcp.server.security.provenance.merkle import MerkleProof, MerkleTree


class TestMerkleTreeBasics:
    def test_empty_tree(self):
        tree = MerkleTree()
        assert tree.root_hash == ""
        assert tree.leaf_count == 0

    def test_single_leaf(self):
        tree = MerkleTree()
        tree.add_leaf("abc123")
        assert tree.root_hash != ""
        assert tree.leaf_count == 1

    def test_two_leaves(self):
        tree = MerkleTree()
        tree.add_leaf("leaf1")
        tree.add_leaf("leaf2")
        assert tree.root_hash != ""
        assert tree.leaf_count == 2

    def test_root_changes_with_new_leaf(self):
        tree = MerkleTree()
        tree.add_leaf("leaf1")
        root1 = tree.root_hash
        tree.add_leaf("leaf2")
        root2 = tree.root_hash
        assert root1 != root2

    def test_deterministic_root(self):
        tree1 = MerkleTree()
        tree1.add_leaf("a")
        tree1.add_leaf("b")

        tree2 = MerkleTree()
        tree2.add_leaf("a")
        tree2.add_leaf("b")

        assert tree1.root_hash == tree2.root_hash

    def test_order_matters(self):
        tree1 = MerkleTree()
        tree1.add_leaf("a")
        tree1.add_leaf("b")

        tree2 = MerkleTree()
        tree2.add_leaf("b")
        tree2.add_leaf("a")

        assert tree1.root_hash != tree2.root_hash

    def test_many_leaves(self):
        tree = MerkleTree()
        for i in range(100):
            tree.add_leaf(f"leaf-{i}")
        assert tree.leaf_count == 100
        assert tree.root_hash != ""


class TestMerkleProof:
    def test_proof_single_leaf(self):
        tree = MerkleTree()
        tree.add_leaf("only-leaf")
        proof = tree.get_proof(0)
        assert proof.verify()

    def test_proof_two_leaves_first(self):
        tree = MerkleTree()
        tree.add_leaf("leaf0")
        tree.add_leaf("leaf1")
        proof = tree.get_proof(0)
        assert proof.verify()

    def test_proof_two_leaves_second(self):
        tree = MerkleTree()
        tree.add_leaf("leaf0")
        tree.add_leaf("leaf1")
        proof = tree.get_proof(1)
        assert proof.verify()

    def test_proof_four_leaves(self):
        tree = MerkleTree()
        for i in range(4):
            tree.add_leaf(f"leaf-{i}")

        for i in range(4):
            proof = tree.get_proof(i)
            assert proof.verify(), f"Proof failed for leaf {i}"

    def test_proof_odd_number_of_leaves(self):
        tree = MerkleTree()
        for i in range(5):
            tree.add_leaf(f"leaf-{i}")

        for i in range(5):
            proof = tree.get_proof(i)
            assert proof.verify(), f"Proof failed for leaf {i}"

    def test_proof_large_tree(self):
        tree = MerkleTree()
        for i in range(37):  # Prime number for non-power-of-2 test
            tree.add_leaf(f"leaf-{i}")

        # Test a sample of proofs
        for i in [0, 1, 17, 36]:
            proof = tree.get_proof(i)
            assert proof.verify(), f"Proof failed for leaf {i}"

    def test_invalid_proof_wrong_root(self):
        tree = MerkleTree()
        tree.add_leaf("leaf0")
        tree.add_leaf("leaf1")
        proof = tree.get_proof(0)

        # Tamper with root
        tampered = MerkleProof(
            leaf_hash=proof.leaf_hash,
            proof_hashes=proof.proof_hashes,
            directions=proof.directions,
            root_hash="tampered_root",
        )
        assert not tampered.verify()

    def test_proof_out_of_range(self):
        tree = MerkleTree()
        tree.add_leaf("leaf0")
        with pytest.raises(IndexError):
            tree.get_proof(1)
        with pytest.raises(IndexError):
            tree.get_proof(-1)


class TestMerkleTreeVerification:
    def test_verify_tree(self):
        tree = MerkleTree()
        for i in range(10):
            tree.add_leaf(f"leaf-{i}")
        assert tree.verify_tree()

    def test_verify_empty_tree(self):
        tree = MerkleTree()
        assert tree.verify_tree()
