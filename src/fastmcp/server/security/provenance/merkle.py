"""Merkle tree for provenance record integrity verification.

Provides efficient batch verification of audit trail integrity.
Leaf nodes are record hashes; the root hash summarizes the entire
tree state. Supports incremental building and proof generation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


def _hash_pair(left: str, right: str) -> str:
    """Hash two child nodes to produce a parent node."""
    combined = (left + right).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


@dataclass(frozen=True)
class MerkleProof:
    """Proof that a leaf exists in a Merkle tree.

    Attributes:
        leaf_hash: The hash of the leaf being proved.
        proof_hashes: Sibling hashes from leaf to root.
        directions: For each proof hash, whether it's on the "left" or "right".
        root_hash: The expected root hash.
    """

    leaf_hash: str
    proof_hashes: list[str] = field(default_factory=list)
    directions: list[str] = field(default_factory=list)
    root_hash: str = ""

    def verify(self) -> bool:
        """Verify this proof against the root hash."""
        current = self.leaf_hash
        for sibling, direction in zip(self.proof_hashes, self.directions):
            if direction == "left":
                current = _hash_pair(sibling, current)
            else:
                current = _hash_pair(current, sibling)
        return current == self.root_hash


class MerkleTree:
    """Incrementally-built Merkle tree over record hashes.

    Supports adding leaves one at a time, computing the root hash,
    and generating inclusion proofs for individual leaves.

    Example::

        tree = MerkleTree()
        tree.add_leaf("abc123")
        tree.add_leaf("def456")
        root = tree.root_hash
        proof = tree.get_proof(0)
        assert proof.verify()
    """

    def __init__(self) -> None:
        self._leaves: list[str] = []
        self._dirty = True
        self._cached_root: str = ""
        self._cached_layers: list[list[str]] = []

    def add_leaf(self, leaf_hash: str) -> int:
        """Add a leaf hash to the tree.

        Args:
            leaf_hash: The hash to add as a leaf node.

        Returns:
            The index of the newly added leaf.
        """
        index = len(self._leaves)
        self._leaves.append(leaf_hash)
        self._dirty = True
        return index

    def _build(self) -> None:
        """Rebuild the tree layers from current leaves."""
        if not self._dirty:
            return

        if not self._leaves:
            self._cached_root = ""
            self._cached_layers = []
            self._dirty = False
            return

        # Layer 0 = leaves
        layers: list[list[str]] = [list(self._leaves)]

        current_layer = layers[0]
        while len(current_layer) > 1:
            next_layer: list[str] = []
            for i in range(0, len(current_layer), 2):
                left = current_layer[i]
                # If odd number of nodes, duplicate the last
                right = current_layer[i + 1] if i + 1 < len(current_layer) else left
                next_layer.append(_hash_pair(left, right))
            layers.append(next_layer)
            current_layer = next_layer

        self._cached_layers = layers
        self._cached_root = layers[-1][0]
        self._dirty = False

    @property
    def root_hash(self) -> str:
        """The Merkle root hash summarizing all leaves."""
        self._build()
        return self._cached_root

    @property
    def leaf_count(self) -> int:
        """Number of leaves in the tree."""
        return len(self._leaves)

    def get_proof(self, leaf_index: int) -> MerkleProof:
        """Generate an inclusion proof for a leaf.

        Args:
            leaf_index: Index of the leaf to prove.

        Returns:
            MerkleProof that can be independently verified.

        Raises:
            IndexError: If leaf_index is out of range.
        """
        if leaf_index < 0 or leaf_index >= len(self._leaves):
            raise IndexError(
                f"Leaf index {leaf_index} out of range [0, {len(self._leaves)})"
            )

        self._build()

        proof_hashes: list[str] = []
        directions: list[str] = []
        idx = leaf_index

        for layer in self._cached_layers[:-1]:  # Skip root layer
            if idx % 2 == 0:
                # Current is left child; sibling is right
                sibling_idx = idx + 1
                if sibling_idx < len(layer):
                    proof_hashes.append(layer[sibling_idx])
                else:
                    proof_hashes.append(layer[idx])  # Duplicate
                directions.append("right")
            else:
                # Current is right child; sibling is left
                proof_hashes.append(layer[idx - 1])
                directions.append("left")
            idx //= 2

        return MerkleProof(
            leaf_hash=self._leaves[leaf_index],
            proof_hashes=proof_hashes,
            directions=directions,
            root_hash=self.root_hash,
        )

    def verify_tree(self) -> bool:
        """Verify internal consistency of the entire tree.

        Rebuilds from leaves and checks that root matches.
        """
        if not self._leaves:
            return True

        old_root = self.root_hash
        self._dirty = True
        self._build()
        return self._cached_root == old_root
