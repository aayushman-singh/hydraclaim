from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hydraclaim.claim_read import ClaimReadLimitError, ClaimReader, ClaimScope
from hydraclaim.errors import GraphIntegrityError
from hydraclaim.extract import parse_claims
from hydraclaim.reconcile import plan_writes

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def claim(
    key="cart-choice",
    *,
    qualifier="cart access",
    value="Keep cart access read-only",
    quote="Keep cart access read-only.",
    **changes,
):
    record = {
        "id": key,
        "subject": "owner",
        "predicate": "decided",
        "qualifier": qualifier,
        "value": value,
        "quote": quote,
        "valid_from": "2026-08-01",
        "valid_to": None,
        "status": "active",
        "author": "owner",
        "source_kind": "chat",
        "session_id": "session",
        "msg_id": "msg",
        "explicitness": 1.0,
        "confidence": 1.0,
        "supersedes": None,
    }
    record.update(changes)
    return record


def response(record):
    return {
        "claims": [
            {
                key: value
                for key, value in record.items()
                if key not in ("id", "valid_to", "status")
            }
        ]
    }


def session(text):
    return {
        "session_id": "session",
        "started_at": "2026-08-01T00:00:00+00:00",
        "messages": [
            {
                "msg_id": "msg",
                "ts": "2026-08-01T00:00:00+00:00",
                "author": "owner",
                "source_kind": "chat",
                "channel": "ops",
                "text": text,
            }
        ],
    }


def test_unrelated_qualified_decisions_remain_independent():
    old = claim()
    new = claim(
        "report-choice",
        qualifier="report cadence",
        value="Use weekly report cadence",
        quote="Use weekly report cadence.",
    )
    plan = plan_writes([new], [old], [])
    assert plan["supersede"] == [] and plan["contradict"] == []
    assert [row["id"] for row in plan["create"]] == ["report-choice"]


def test_duplicate_values_in_different_qualified_choices_are_not_dropped():
    old = claim(value="enabled")
    new = claim(
        "report-choice",
        qualifier="report cadence",
        value="enabled",
        quote="The report cadence is enabled.",
    )
    plan = plan_writes([new], [old], [])
    assert plan["duplicates"] == 0
    assert len(plan["create"]) == 1


def test_a_changed_value_in_same_qualified_scope_needs_explicit_correction():
    old = claim()
    new = claim(
        "changed-choice",
        value="Permit cart access changes",
        quote="Permit cart access changes.",
        valid_from="2026-08-02",
    )
    plan = plan_writes([new], [old], [])
    assert plan["supersede"] == []
    assert plan["contradict"] == [{"a_id": "cart-choice", "b_id": "changed-choice"}]


def test_explicit_qualified_correction_replaces_only_its_scope():
    old = claim()
    new = claim(
        "changed-choice",
        value="Permit cart access changes",
        quote="Permit cart access changes.",
        supersedes=old["id"],
    )
    plan = plan_writes([new], [old], [])
    assert plan["supersede"] == [
        {"new_id": "changed-choice", "old_id": old["id"], "at": new["valid_from"]}
    ]


def test_cross_qualified_explicit_correction_fails():
    old = claim()
    new = claim(
        "report-choice",
        qualifier="report cadence",
        value="Use weekly report cadence",
        quote="Use weekly report cadence.",
        supersedes=old["id"],
    )
    with pytest.raises(ValueError, match="scope"):
        plan_writes([new], [old], [])


def test_extraction_preserves_source_backed_canonical_qualifier():
    original = claim(qualifier="Cart access", quote="Cart access must stay read-only.")
    drafts, warnings = parse_claims(response(original), session(original["quote"]), [])
    assert warnings == []
    assert drafts[0]["qualifier"] == "cart access"


@pytest.mark.parametrize("qualifier", [None, "", "   ", 7, {"value": "cart access"}])
def test_missing_or_malformed_qualification_fails_before_reconciliation(qualifier):
    record = claim(qualifier=qualifier)
    with pytest.raises(ValueError, match="qualifier"):
        parse_claims(response(record), session(record["quote"]), [])


def test_scope_phrase_must_come_from_the_claim_evidence():
    record = claim(qualifier="report cadence")
    with pytest.raises(ValueError, match="qualifier"):
        parse_claims(response(record), session(record["quote"]), [])


def test_shared_broad_evidence_refuses_swapped_qualifier_value_pairs():
    text = "Cart access must stay read-only. Report cadence must stay weekly."
    first = claim(
        qualifier="Cart access", value="Keep report cadence weekly", quote=text
    )
    second = claim(
        "report-choice",
        qualifier="Report cadence",
        value="Keep cart access read-only",
        quote=text,
    )
    raw = response(first)
    raw["claims"].extend(response(second)["claims"])
    with pytest.raises(ValueError, match="ambiguous qualified evidence"):
        parse_claims(raw, session(text), [])


def test_two_decisions_in_one_message_use_separate_supported_evidence():
    first = claim(qualifier="Cart access", quote="Cart access must stay read-only.")
    second = claim(
        "report-choice",
        qualifier="Report cadence",
        value="Keep report cadence weekly",
        quote="Report cadence must stay weekly.",
    )
    raw = response(first)
    raw["claims"].extend(response(second)["claims"])
    drafts, warnings = parse_claims(
        raw, session(first["quote"] + " " + second["quote"]), []
    )
    assert warnings == []
    assert {row["qualifier"] for row in drafts} == {"cart access", "report cadence"}


class DecisionDB:
    def __init__(self):
        shared = "Cart access must stay read-only. Report cadence must stay weekly."
        self.rows = [
            {
                **claim(),
                "id": 1,
                "key": "cart-choice",
                "valid_to": "",
                "quote": shared,
                "extraction_confidence": 1.0,
            },
            {
                **claim(
                    "report-choice",
                    qualifier="report cadence",
                    value="Keep report cadence weekly",
                ),
                "id": 2,
                "key": "report-choice",
                "valid_to": "",
                "quote": shared,
                "extraction_confidence": 1.0,
            },
        ]
        self.queries = []

    def query(self, cypher, consistency="causal"):
        self.queries.append(cypher)
        if "MATCH (e:Entity)" in cypher:
            return [{"name": "owner", "aliases": ""}]
        if "RETURN c.id AS id, e.name AS subject, c.predicate AS predicate" in cypher:
            return [dict(row) for row in self.rows if f"{{id: {row['id']}}}" in cypher]
        if "c.key AS key" in cypher:
            return list(self.rows)
        return []


def test_stored_scope_selects_only_correct_claim_even_with_shared_quote():
    result = ClaimReader(DecisionDB()).answer(
        "What did the owner decide about cart access?", now=NOW
    )
    assert result.route == "FAST"
    assert [row.claim_id for row in result.citations] == ["cart-choice"]
    assert result.citations[0].qualifier == "cart access"
    assert "Keep report cadence weekly" not in result.text


def test_unknown_specific_scope_abstains_without_generic_substitution():
    result = ClaimReader(DecisionDB()).answer(
        "What did the owner decide about birthday alerts?", now=NOW
    )
    assert result.route == "ABSTAIN"
    assert result.citations == ()


def test_generic_listing_retains_both_scopes_even_when_values_match():
    db = DecisionDB()
    for row in db.rows:
        row["value"] = "enabled"
    from hydraclaim.claims import QualifierSelection

    result = ClaimReader(db).answer(
        "What did the owner decide?", now=NOW, qualifier=QualifierSelection.ALL
    )
    assert {row.claim_id for row in result.citations} == {
        "cart-choice",
        "report-choice",
    }
    assert "conflict" not in result.text.lower()
    assert {row.qualifier for row in result.citations} == {
        "cart access",
        "report cadence",
    }


def test_legacy_qualified_predicate_without_scope_is_an_integrity_failure():
    db = DecisionDB()
    del db.rows[1]["qualifier"]
    with pytest.raises(GraphIntegrityError, match="qualifier"):
        ClaimReader(db).read_claims(
            ClaimScope("owner", "decided", qualifier="cart access")
        )


def test_scope_filter_does_not_hide_an_exceeded_candidate_bound():
    with pytest.raises(ClaimReadLimitError):
        ClaimReader(DecisionDB()).read_claims(
            ClaimScope("owner", "decided", limit=1, qualifier="cart access")
        )


def test_missing_qualified_selection_cannot_mean_all_scopes():
    with pytest.raises(ValueError, match="qualifier"):
        ClaimScope("owner", "decided")


def test_unknown_specific_scope_is_not_an_all_scope_request():
    result = ClaimReader(DecisionDB()).answer(
        "What did the owner decide for an unnamed choice?", now=NOW
    )
    assert result.route == "ABSTAIN"
    assert result.citations == ()


def test_empty_qualifier_is_not_all_scopes():
    with pytest.raises(ValueError, match="qualifier"):
        ClaimScope("owner", "decided", qualifier=" ")


def test_qualifier_phrase_does_not_match_inside_a_word():
    record = claim(qualifier="art access")
    with pytest.raises(ValueError, match="qualifier"):
        parse_claims(response(record), session(record["quote"]), [])


def test_disagreeing_question_and_explicit_selection_abstains():
    result = ClaimReader(DecisionDB()).answer(
        "What did the owner decide about cart access?",
        qualifier="report cadence",
        now=NOW,
    )
    assert result.route == "ABSTAIN"
    assert result.citations == ()


def test_regarding_selects_the_exact_qualified_claim():
    result = ClaimReader(DecisionDB()).answer(
        "What did the owner decide regarding cart access?", now=NOW
    )
    assert [citation.claim_id for citation in result.citations] == ["cart-choice"]


def test_missing_qualified_correction_target_fails():
    draft = claim(supersedes="missing")
    with pytest.raises(ValueError, match="supersedes target"):
        plan_writes([draft], [], [], id_prefix="test")


@pytest.mark.parametrize("marker", ["", 1, False, [], {}])
def test_malformed_invalidation_marker_fails(marker):
    from hydraclaim.claim_read import _invalidated_relation

    with pytest.raises(GraphIntegrityError, match="invalidation"):
        _invalidated_relation({"invalidated_by": marker})


def test_absent_relation_marker_remains_valid_and_explicit_marker_is_invalid():
    from hydraclaim.claim_read import _invalidated_relation

    assert not _invalidated_relation({})
    assert not _invalidated_relation({"invalidated_by": None})
    assert _invalidated_relation({"invalidated_by": "repair-1"})


def test_generic_listing_preserves_conflict_within_one_qualifier():
    from hydraclaim.claims import QualifierSelection

    db = DecisionDB()
    db.rows[1]["qualifier"] = "cart access"
    result = ClaimReader(db).answer(
        "What did the owner decide?", qualifier=QualifierSelection.ALL, now=NOW
    )
    assert "Unresolved conflict" in result.text
    assert len(result.citations) == 2


def test_temporal_selection_removes_the_recognized_comparison_clause():
    result = ClaimReader(DecisionDB()).answer(
        "What did the owner decide about cart access before the most recent change?",
        now=NOW,
    )
    assert result.probe.coverage == 1
    assert result.route == "ABSTAIN"
    assert result.citations == ()


def test_active_conflicting_claims_do_not_prove_a_temporal_change():
    db = DecisionDB()
    db.rows[1]["qualifier"] = "cart access"
    result = ClaimReader(db).answer(
        "What did the owner decide about cart access before the most recent change?",
        now=NOW,
    )
    assert result.route == "ABSTAIN"
    assert result.citations == ()
