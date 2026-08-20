"""Pure-helper tests for the HydraDB dialect model mappings (no live DB)."""

from hydraclaim.model import (
    OPEN,
    claim_props,
    entity_props,
    graph_id,
    join_aliases,
    slug,
    split_aliases,
)
from hydraclaim.probe import _chain_depth


class TestGraphId:
    def test_deterministic(self):
        assert graph_id("payments:c1") == graph_id("payments:c1")

    def test_is_int_within_62_bits(self):
        gid = graph_id("payments:c1")
        assert isinstance(gid, int)
        assert 0 <= gid < 2**62

    def test_distinct_keys_give_distinct_ids(self):
        assert graph_id("payments:c1") != graph_id("payments:c2")


class TestAliases:
    def test_roundtrip(self):
        assert split_aliases(join_aliases(["Priya", "priya.shah"])) == [
            "Priya",
            "priya.shah",
        ]

    def test_empty(self):
        assert join_aliases([]) == ""
        assert split_aliases("") == []
        assert split_aliases(None) == []

    def test_skips_empty_entries(self):
        assert join_aliases(["a", "", "b"]) == "a|b"


class TestClaimProps:
    def test_open_validity_window(self):
        claim = {
            "subject": "payments",
            "predicate": "owner",
            "value": "Priya Shah",
            "valid_from": "2026-05-01",
            "quote": "q",
            "source_kind": "slack",
            "author": "dario",
        }
        props = claim_props(claim, "payments:c1", "2026-05-01T00:00:00Z")
        assert props["valid_to"] == OPEN
        assert props["valid_to"] == ""
        assert props["status"] == "active"
        assert props["id"] == graph_id("payments:c1")
        assert props["key"] == "payments:c1"
        assert props["subject"] == "payments"

    def test_closed_window_preserved(self):
        claim = {
            "predicate": "deadline",
            "value": "2026-06-01",
            "valid_from": "2026-05-01",
            "valid_to": "2026-05-14",
            "quote": "q",
            "source_kind": "slack",
            "author": "dario",
        }
        assert claim_props(claim, "k", "r")["valid_to"] == "2026-05-14"


class TestEntityProps:
    def test_aliases_stored_pipe_delimited(self):
        props = entity_props(
            "payments", "Payments Integration", "system", ["payments", "payments-int"]
        )
        assert props["aliases"] == "payments|payments-int"
        assert props["id"] == graph_id("payments:payments-integration")


def test_slug():
    assert slug("Priya Shah (Eng)") == "priya-shah-eng"


class TestChainDepth:
    def test_empty(self):
        assert _chain_depth([], {1, 2}) == 0

    def test_linear_chain_of_three(self):
        # 3 supersedes 2, 2 supersedes 1 -> longest path is 2 edges.
        assert _chain_depth([(3, 2), (2, 1)], {1, 2, 3}) == 2

    def test_edges_outside_id_set_ignored(self):
        # Edge (9 -> 3) touches the set but 9 is not in it; only 2 -> 1 counts.
        edges = [(9, 3), (2, 1)]
        assert _chain_depth(edges, {1, 2, 3}) == 1

    def test_branched_takes_longest(self):
        edges = [(4, 3), (3, 2), (2, 1), (4, 5)]
        assert _chain_depth(edges, {1, 2, 3, 4, 5}) == 3
