import hashlib
import re

import pytest

from hydraclaim.errors import GraphIntegrityError
from hydraclaim.claim_read import ClaimReader, ClaimScope
from hydraclaim.graph_write import (
    GraphWriter,
    GraphEventReviewSource,
    LegacyQualificationReview,
    PublicGitReviewSource,
    QualificationClaimRepair,
    QualificationEdgeRepair,
    QualificationRepair,
    QualificationRepairApplyError,
    _claim_metadata,
    _validate_claim,
    _validate_supersession_integrity,
)
from hydraclaim.model import claim_props, graph_id, source_key


class NoWriteDB:
    def __init__(self):
        self.queries = []
        self.writes = []

    def query(self, cypher):
        self.queries.append(cypher)
        if cypher.lstrip().startswith("CREATE") or " SET " in cypher:
            self.writes.append(cypher)
        return []

    def query_one(self, cypher):
        return {"c": 0}


class MetadataDB(NoWriteDB):
    def __init__(self, row):
        super().__init__()
        self.row = row

    def query_one(self, cypher):
        if "RETURN c.subject AS subject" in cypher:
            return self.row
        return super().query_one(cypher)


class RepairDB(NoWriteDB):
    def __init__(
        self,
        claims,
        edges=(),
        fail_on_write=None,
        quoted_from_counts=None,
        zero_match_write=None,
        commit_then_raise=None,
    ):
        super().__init__()
        self.claims = {graph_id(key): dict(row) for key, row in claims.items()}
        for claim_key in claims:
            row = self.claims[graph_id(claim_key)]
            evidence_key = f"{claim_key}:ev0"
            source_identity = source_key(row["source_kind"], row["author"])
            row["evidence_id"] = graph_id(evidence_key)
            row["evidence_key"] = evidence_key
            row["source_id"] = graph_id(source_identity)
            row["source_key"] = source_identity
        self.edges = {
            (graph_id(new_key), graph_id(old_key)): dict(row)
            for new_key, old_key, row in edges
        }
        self.fail_on_write = fail_on_write
        self.zero_match_write = zero_match_write
        self.commit_then_raise = commit_then_raise
        self.quoted_from_counts = quoted_from_counts or {}

    def query(self, cypher):
        self.queries.append(cypher)
        if "MATCH (ev:Evidence" in cypher and "QUOTED_FROM" in cypher:
            evidence_id = int(re.search(r"id: (\d+)", cypher).group(1))
            row = next(
                (
                    claim
                    for claim in self.claims.values()
                    if claim["evidence_id"] == evidence_id
                ),
                None,
            )
            count = self.quoted_from_counts.get(row["key"], 1) if row else 0
            return (
                [{"source_event_key": row["source_event_key"]} for _ in range(count)]
                if row is not None
                else []
            )
        if "MATCH (event:SourceEvent" in cypher:
            event_key = re.search(r"key: '([^']*)'", cypher).group(1)
            row = next(
                (
                    claim
                    for claim in self.claims.values()
                    if claim["source_event_key"] == event_key
                ),
                None,
            )
            return (
                [
                    {
                        "source_event_key": row["source_event_key"],
                        "source_content": row["source_content"],
                        "event_source_kind": row["source_kind"],
                        "event_author": row["author"],
                    }
                ]
                if row is not None
                else []
            )
        if "RETURN c.key AS key" in cypher:
            claim_id = int(re.search(r"id: (\d+)", cypher).group(1))
            row = self.claims.get(claim_id)
            return [dict(row)] if row is not None else []
        if "RETURN r.at AS at" in cypher:
            ids = [int(value) for value in re.findall(r"id: (\d+)", cypher)]
            row = self.edges.get((ids[0], ids[1]))
            return [dict(row)] if row is not None else []
        if " SET " in cypher:
            if self.fail_on_write == len(self.writes) + 1:
                raise RuntimeError("simulated write failure")
            self.writes.append(cypher)
            write_number = len(self.writes)
            if self.zero_match_write == write_number:
                return []
            if "qualified_by_repair" in cypher:
                claim_id = int(re.search(r"id: (\d+)", cypher).group(1))
                row = self.claims[claim_id]
                qualifier = re.search(r"c.qualifier = '([^']*)'", cypher).group(1)
                marker = re.search(r"c.qualified_by_repair = '([^']*)'", cypher).group(
                    1
                )
                row["qualifier"] = qualifier
                row["qualified_by_repair"] = marker
                status = re.search(r"c.status = '([^']*)'", cypher)
                valid_to = re.search(r"c.valid_to = '([^']*)'", cypher)
                if status:
                    row["status"] = status.group(1)
                if valid_to:
                    row["valid_to"] = valid_to.group(1)
            elif "r.invalidated_by" in cypher:
                ids = [int(value) for value in re.findall(r"id: (\d+)", cypher)]
                marker = re.search(r"invalidated_by = '([^']*)'", cypher).group(1)
                self.edges[(ids[0], ids[1])]["invalidated_by"] = marker
            if self.commit_then_raise == write_number:
                raise RuntimeError("simulated commit then raise")
            return []
        return super().query(cypher)


def _repair_claim(
    key,
    qualifier,
    *,
    status="superseded",
    valid_to="2026-10-30",
    expected_quote=None,
    legacy_review=None,
    expected_source_event_key="default",
):
    quote = expected_quote or f"{qualifier}: preserve the original choice."
    return QualificationClaimRepair(
        key=key,
        expected_subject="owner",
        expected_predicate="decided",
        expected_value="preserve the original choice",
        expected_valid_from="2026-10-29",
        expected_valid_to=valid_to,
        expected_status=status,
        expected_quote=quote,
        expected_source_kind="chat",
        expected_author="Owner",
        expected_source_event_key=(
            f"event-{key}"
            if expected_source_event_key == "default"
            else expected_source_event_key
        ),
        qualifier=qualifier,
        restore_status="active" if key == "cart" else None,
        restore_valid_to="" if key == "cart" else None,
        legacy_review=legacy_review,
    )


def _repair_row(key, qualifier=None, *, status="superseded", valid_to="2026-10-30"):
    quote = f"{'cart access' if key == 'cart' else 'report cadence'}: preserve the original choice."
    return {
        "key": key,
        "subject": "owner",
        "predicate": "decided",
        "qualifier": qualifier,
        "value": "preserve the original choice",
        "valid_from": "2026-10-29",
        "valid_to": valid_to,
        "status": status,
        "qualified_by_repair": None,
        "quote": quote,
        "source_kind": "chat",
        "author": "Owner",
        "source_event_key": f"event-{key}",
        "source_content": quote,
    }


def _repair():
    return QualificationRepair(
        repair_id="repair-2026-11-02",
        claims=(
            _repair_claim("cart", "cart access"),
            _repair_claim("report", "report cadence"),
        ),
        invalidated_edges=(QualificationEdgeRepair("report", "cart", "2026-10-30"),),
    )


def _claim(claim_id, qualifier, value, predicate="decided"):
    return {
        "id": claim_id,
        "subject": "owner",
        "predicate": predicate,
        "qualifier": qualifier,
        "value": value,
        "valid_from": "2026-10-29",
        "quote": f"{qualifier}: {value}.",
        "author": "Owner",
        "source_kind": "chat",
        "confidence": 1.0,
        "explicitness": 1.0,
    }


def _plan(claims, supersede=None, contradict=None):
    return {
        "create": claims,
        "supersede": supersede or [],
        "contradict": contradict or [],
        "duplicates": 0,
        "warnings": [],
    }


def test_claim_properties_store_canonical_qualifier_and_omit_unqualified_scope():
    scoped = _claim("decision", "Cart   Access", "Keep access read-only")
    status = _claim("status", None, "active", predicate="status")

    assert claim_props(scoped, "decision", "now")["qualifier"] == "cart access"
    status_properties = claim_props(status, "status", "now")
    assert "qualifier" not in status_properties


def test_unqualified_claim_write_properties_round_trip_through_claim_reader():
    claim = _claim("status", None, "active", predicate="status")
    properties = claim_props(claim, "status", "2026-11-02")
    row = {
        **properties,
        "quote": claim["quote"],
        "explicitness": claim["explicitness"],
        "extraction_confidence": claim["confidence"],
        "source_kind": claim["source_kind"],
        "author": claim["author"],
    }

    view = ClaimReader._to_claim_view(
        row, ClaimScope(subject="owner", predicate="status"), 1
    )

    assert view.qualifier is None
    assert "qualifier" not in properties


@pytest.mark.parametrize("qualifier", [None, "", "  ", 4])
def test_writer_rejects_missing_or_invalid_required_qualifier(qualifier):
    errors = []
    claim = _claim("decision", qualifier, "Keep access read-only")

    _validate_claim(claim, "claim", errors)

    assert any("qualifier" in error for error in errors)


def test_writer_rejects_qualifier_on_single_valued_predicate():
    errors = []
    claim = _claim("status", "cart access", "active", predicate="status")

    _validate_claim(claim, "claim", errors)

    assert any("qualifier" in error for error in errors)


def test_writer_rejects_noncanonical_stored_qualifier():
    errors = []
    claim = _claim("decision", "Cart Access", "Keep access read-only")

    _validate_claim(claim, "claim", errors)

    assert any("canonical" in error for error in errors)


def test_supersession_must_match_full_qualified_scope_before_any_write():
    db = NoWriteDB()
    plan = _plan(
        [
            _claim("cart", "cart access", "Keep cart access read-only"),
            _claim("report", "report cadence", "Use weekly reports"),
        ],
        supersede=[{"new_id": "report", "old_id": "cart", "at": "2026-10-29"}],
    )

    with pytest.raises(GraphIntegrityError, match="same qualifier"):
        GraphWriter(db).apply_plan(plan, "scope-test")

    assert db.writes == []


def test_contradiction_must_match_full_qualified_scope_before_any_write():
    db = NoWriteDB()
    plan = _plan(
        [
            _claim("cart", "cart access", "Keep cart access read-only"),
            _claim("report", "report cadence", "Use daily reports"),
        ],
        contradict=[{"a_id": "cart", "b_id": "report"}],
    )

    with pytest.raises(GraphIntegrityError, match="same qualifier"):
        GraphWriter(db).apply_plan(plan, "scope-test")

    assert db.writes == []


def test_same_qualified_scope_allows_supersession_validation():
    db = NoWriteDB()
    claims = {
        "old": ("owner", "decided", "cart access"),
        "new": ("owner", "decided", "cart access"),
    }

    _validate_supersession_integrity(
        db,
        [{"new_id": "new", "old_id": "old", "at": "2026-10-29"}],
        claims,
    )

    assert db.writes == []


@pytest.mark.parametrize(
    ("marker", "raises"), [("repair-v1", False), ("", True), (" ", True)]
)
def test_supersession_cycle_check_uses_only_active_edges(marker, raises):
    class EdgeDB(NoWriteDB):
        def query(self, cypher):
            self.queries.append(cypher)
            if "RETURN a.id AS new_id" in cypher:
                current = int(re.search(r"id: (\d+)", cypher).group(1))
                if current == graph_id("old"):
                    return [
                        {
                            "new_id": graph_id("old"),
                            "old_id": graph_id("new"),
                            "invalidated_by": marker,
                        }
                    ]
            return []

    db = EdgeDB()
    claims = {key: ("owner", "decided", "service language") for key in ("old", "new")}
    planned = [{"new_id": "new", "old_id": "old", "at": "2026-10-30"}]
    if raises:
        with pytest.raises(GraphIntegrityError, match="malformed invalidated_by"):
            _validate_supersession_integrity(db, planned, claims)
    else:
        _validate_supersession_integrity(db, planned, claims)


def test_stored_legacy_claim_without_required_qualifier_fails_scope_validation():
    db = MetadataDB({"subject": "owner", "predicate": "decided", "qualifier": None})

    with pytest.raises(GraphIntegrityError, match="qualifier"):
        _claim_metadata(db, "legacy-decision")


def test_stored_endpoint_qualifier_participates_in_supersession_validation():
    db = MetadataDB(
        {"subject": "owner", "predicate": "decided", "qualifier": "cart access"}
    )

    with pytest.raises(GraphIntegrityError, match="same qualifier"):
        _validate_supersession_integrity(
            db,
            [{"new_id": "new", "old_id": "old", "at": "2026-10-29"}],
            {"new": ("owner", "decided", "report cadence")},
        )

    assert db.writes == []


def test_qualification_repair_dry_run_preflights_exact_source_and_all_claims():
    db = RepairDB(
        {
            "cart": _repair_row("cart"),
            "report": _repair_row("report"),
        },
        edges=[
            (
                "report",
                "cart",
                {"at": "2026-10-30", "invalidated_by": None},
            )
        ],
    )

    receipt = GraphWriter(db).repair_qualifications(_repair())

    assert receipt.dry_run is True
    assert len(receipt.claims_before) == 2
    assert receipt.claims_after is None
    assert receipt.claims_planned[0].qualifier == "cart access"
    assert receipt.claims_planned[0].status == "active"
    assert receipt.claims_planned[0].valid_to == ""
    assert len(receipt.invalidated_edges) == 1
    assert receipt.edges_before[0].invalidated_by is None
    assert receipt.edges_after is None
    assert receipt.edges_planned[0].invalidated_by == receipt.repair_id
    assert db.writes == []
    claim_reads = [query for query in db.queries if "RETURN c.key AS key" in query]
    provenance_reads = [query for query in db.queries if "QUOTED_FROM" in query]
    event_reads = [query for query in db.queries if "MATCH (event:SourceEvent" in query]
    assert len(claim_reads) == len(provenance_reads) == len(event_reads) == 2
    assert all(
        "OPTIONAL MATCH" not in query
        for query in claim_reads + provenance_reads + event_reads
    )


def test_qualification_repair_preflights_every_claim_before_mutation():
    invalid = _repair_row("report")
    invalid["value"] = "changed value"
    db = RepairDB(
        {"cart": _repair_row("cart"), "report": invalid},
        edges=[
            (
                "report",
                "cart",
                {"at": "2026-10-30", "invalidated_by": None},
            )
        ],
    )

    with pytest.raises(GraphIntegrityError, match="preflight mismatch"):
        GraphWriter(db).repair_qualifications(_repair(), dry_run=False)

    assert db.writes == []


def test_qualification_repair_updates_only_claim_scope_validity_and_edge_marker():
    db = RepairDB(
        {
            "cart": _repair_row("cart"),
            "report": _repair_row("report"),
        },
        edges=[
            (
                "report",
                "cart",
                {"at": "2026-10-30", "invalidated_by": None},
            )
        ],
    )

    receipt = GraphWriter(db).repair_qualifications(_repair(), dry_run=False)

    assert receipt.dry_run is False
    assert db.claims[graph_id("cart")]["qualifier"] == "cart access"
    assert db.claims[graph_id("cart")]["status"] == "active"
    assert db.claims[graph_id("cart")]["valid_to"] == ""
    assert db.claims[graph_id("report")]["qualifier"] == "report cadence"
    assert db.claims[graph_id("report")]["status"] == "superseded"
    assert (
        db.edges[(graph_id("report"), graph_id("cart"))]["invalidated_by"]
        == _repair().repair_id
    )
    assert len(db.writes) == 3


def test_qualification_repair_replay_is_idempotent():
    repair = _repair()
    cart = _repair_row("cart", "cart access", status="active", valid_to="")
    cart["qualified_by_repair"] = repair.repair_id
    report = _repair_row("report", "report cadence")
    report["qualified_by_repair"] = repair.repair_id
    db = RepairDB(
        {"cart": cart, "report": report},
        edges=[
            (
                "report",
                "cart",
                {"at": "2026-10-30", "invalidated_by": repair.repair_id},
            )
        ],
    )

    receipt = GraphWriter(db).repair_qualifications(repair, dry_run=False)

    assert receipt.effects == ()
    assert db.writes == []


def test_qualification_repair_captures_partial_effects_and_exact_states():
    db = RepairDB(
        {"cart": _repair_row("cart"), "report": _repair_row("report")},
        edges=[
            (
                "report",
                "cart",
                {"at": "2026-10-30", "invalidated_by": None},
            )
        ],
        fail_on_write=2,
    )

    with pytest.raises(QualificationRepairApplyError) as captured:
        GraphWriter(db).repair_qualifications(_repair(), dry_run=False)

    assert captured.value.failed_step == "qualify Claim report"
    assert captured.value.effects == ("qualified Claim cart",)
    assert len(captured.value.claims_before) == 2
    assert len(captured.value.claims_after) == 2


def test_repair_zero_match_write_fails_with_actual_claim_state():
    db = RepairDB(
        {"cart": _repair_row("cart"), "report": _repair_row("report")},
        edges=[("report", "cart", {"at": "2026-10-30", "invalidated_by": None})],
        zero_match_write=1,
    )
    with pytest.raises(QualificationRepairApplyError) as captured:
        GraphWriter(db).repair_qualifications(_repair(), dry_run=False)
    assert captured.value.claims_after[0].qualifier is None
    assert captured.value.effects == (
        "qualified Claim report",
        "invalidated SUPERSEDES report->cart",
    )


def test_repair_zero_match_readback_keeps_claim_order_and_later_effects():
    db = RepairDB(
        {"cart": _repair_row("cart"), "report": _repair_row("report")},
        edges=[("report", "cart", {"at": "2026-10-30", "invalidated_by": None})],
        zero_match_write=1,
    )
    with pytest.raises(QualificationRepairApplyError) as captured:
        GraphWriter(db).repair_qualifications(_repair(), dry_run=False)

    assert len(captured.value.claims_after) == 2
    assert captured.value.claims_after[0].key == "cart"
    assert captured.value.claims_after[0].qualifier is None
    assert captured.value.claims_after[1].key == "report"
    assert captured.value.claims_after[1].qualifier == "report cadence"
    assert captured.value.effects == (
        "qualified Claim report",
        "invalidated SUPERSEDES report->cart",
    )


def test_repair_commit_then_raise_reports_confirmed_state_and_uncertain_step():
    db = RepairDB(
        {"cart": _repair_row("cart"), "report": _repair_row("report")},
        edges=[("report", "cart", {"at": "2026-10-30", "invalidated_by": None})],
        commit_then_raise=1,
    )
    with pytest.raises(QualificationRepairApplyError) as captured:
        GraphWriter(db).repair_qualifications(_repair(), dry_run=False)
    assert captured.value.claims_after[0].qualifier == "cart access"
    assert captured.value.uncertain_step == "qualify Claim cart"
    assert captured.value.effects == ("qualified Claim cart",)
    assert captured.value.edges_before[0].invalidated_by is None


def test_qualification_repair_requires_qualifier_in_source_quote():
    repair = _repair()
    bad_claim = QualificationClaimRepair(
        **{**repair.claims[0].__dict__, "expected_quote": "A choice was recorded."}
    )
    invalid = QualificationRepair(
        repair_id=repair.repair_id,
        claims=(bad_claim, repair.claims[1]),
        invalidated_edges=repair.invalidated_edges,
    )

    with pytest.raises(ValueError, match="anchored"):
        GraphWriter(NoWriteDB()).repair_qualifications(invalid)


def test_reviewed_legacy_semantic_mapping_pins_source_and_preserves_quote():
    source_content = "Samir Ali prefers TypeScript for greenfield services."
    original_quote = "Samir Ali prefers TypeScript"
    claim_key = "event-cart:x1"
    source_identity = source_key("chat", "Owner")
    review = LegacyQualificationReview(
        review_identity="review-legacy-preferences-v1",
        expected_evidence_id=graph_id(f"{claim_key}:ev0"),
        expected_evidence_key=f"{claim_key}:ev0",
        expected_source_id=graph_id(source_identity),
        expected_source_key=source_identity,
        rationale="The pinned source maps this TypeScript service-language preference to the same scope as the later Go preference.",
        source=GraphEventReviewSource(
            source_event_key="event-cart",
            expected_quoted_from_count=0,
            source_content_sha256=hashlib.sha256(
                source_content.encode("utf-8")
            ).hexdigest(),
        ),
    )
    reviewed_claim = _repair_claim(
        claim_key,
        "service language",
        expected_quote=original_quote,
        legacy_review=review,
        expected_source_event_key="event-cart",
    )
    cart_row = _repair_row(claim_key)
    cart_row["quote"] = original_quote
    cart_row["source_content"] = source_content
    cart_row["source_event_key"] = "event-cart"
    repair = QualificationRepair(
        repair_id="reviewed-legacy-repair-v1",
        claims=(reviewed_claim, _repair_claim("report", "report cadence")),
        invalidated_edges=(QualificationEdgeRepair("report", claim_key, "2026-10-30"),),
    )
    db = RepairDB(
        {claim_key: cart_row, "report": _repair_row("report")},
        edges=[
            (
                "report",
                claim_key,
                {"at": "2026-10-30", "invalidated_by": None},
            )
        ],
        quoted_from_counts={claim_key: 0},
    )

    receipt = GraphWriter(db).repair_qualifications(repair, dry_run=False)

    assert receipt.legacy_reviews == (review,)
    assert db.claims[graph_id(claim_key)]["qualifier"] == "service language"
    assert db.claims[graph_id(claim_key)]["quote"] == original_quote


def test_public_git_review_pins_source_identity_with_no_graph_event_link():
    claim_key = "multi_vendor_decision:mv-2"
    source_bytes = (
        b'_ev(5, "s2", "slack", "Tara Ellis", "#email", '
        b'"Pushed back in the thread: Mailgun is the better deal and we should '
        b'switch before contracting.")'
    )
    event_content = (
        "Pushed back in the thread: Mailgun is the better deal and we should "
        "switch before contracting."
    )
    evidence_key = f"{claim_key}:ev0"
    source_identity = source_key("slack", "Tara Ellis")
    quote = "Mailgun is the better deal"
    review = LegacyQualificationReview(
        review_identity="review-public-seed-map-v1",
        expected_evidence_id=graph_id(evidence_key),
        expected_evidence_key=evidence_key,
        expected_source_id=graph_id(source_identity),
        expected_source_key=source_identity,
        rationale="The reviewed #email seed event and the Claim subject identify this as the email platform choice.",
        source=PublicGitReviewSource(
            commit="c323131198a394e2b2288852f96260731cb171a9",
            path="hydraclaim/generate/scenarios.py",
            scenario_id="multi_vendor_decision",
            claim_key="mv-2",
            channel="#email",
            event_content=event_content,
            event_content_sha256=hashlib.sha256(
                event_content.encode("utf-8")
            ).hexdigest(),
            source_file_bytes=source_bytes,
            source_file_sha256=hashlib.sha256(source_bytes).hexdigest(),
            expected_quoted_from_count=0,
        ),
    )
    repair_claim = QualificationClaimRepair(
        key=claim_key,
        expected_subject="email platform",
        expected_predicate="decided",
        expected_value="Mailgun",
        expected_valid_from="2026-06-06",
        expected_valid_to="",
        expected_status="active",
        expected_quote=quote,
        expected_source_kind="slack",
        expected_author="Tara Ellis",
        expected_source_event_key=None,
        qualifier="email platform",
        legacy_review=review,
    )
    row = {
        **_repair_row(claim_key),
        "subject": "email platform",
        "value": "Mailgun",
        "valid_from": "2026-06-06",
        "valid_to": "",
        "status": "active",
        "quote": quote,
        "source_kind": "slack",
        "author": "Tara Ellis",
        "source_content": event_content,
        "source_event_key": None,
    }
    db = RepairDB({claim_key: row}, quoted_from_counts={claim_key: 0})
    repair = QualificationRepair(
        repair_id="review-public-seed-map-v1",
        claims=(repair_claim,),
    )

    with pytest.raises(GraphIntegrityError, match="trusted source verifier"):
        GraphWriter(db).repair_qualifications(repair, dry_run=True)
    with pytest.raises(GraphIntegrityError, match="rejected"):
        GraphWriter(db).repair_qualifications(
            repair, dry_run=True, public_source_verifier=lambda *_: False
        )
    assert db.writes == []

    called = []
    receipt = GraphWriter(db).repair_qualifications(
        repair,
        dry_run=True,
        public_source_verifier=lambda claim, source: (
            called.append((claim, source)) or True
        ),
    )

    assert receipt.legacy_reviews == (review,)
    assert db.writes == []
    assert called == [(repair_claim, review.source)]
    assert not any("MATCH (event:SourceEvent" in query for query in db.queries)


def test_reviewed_legacy_mapping_rejects_source_digest_or_identity_mismatch():
    source_content = "Samir Ali prefers TypeScript for greenfield services."
    source_identity = source_key("chat", "Owner")
    review = LegacyQualificationReview(
        review_identity="review-legacy-preferences-v1",
        expected_evidence_id=graph_id("event-cart:x1:ev0"),
        expected_evidence_key="event-cart:x1:ev0",
        expected_source_id=graph_id(source_identity),
        expected_source_key=source_identity,
        rationale="Reviewed semantic mapping to service language.",
        source=GraphEventReviewSource(
            source_event_key="event-cart",
            expected_quoted_from_count=0,
            source_content_sha256=hashlib.sha256(
                source_content.encode("utf-8")
            ).hexdigest(),
        ),
    )
    reviewed_claim = _repair_claim(
        "event-cart:x1",
        "service language",
        expected_quote="Samir Ali prefers TypeScript",
        legacy_review=review,
        expected_source_event_key="event-cart",
    )
    row = _repair_row("event-cart:x1")
    row["quote"] = "Samir Ali prefers TypeScript"
    row["source_content"] = source_content + " Extra text."
    row["source_event_key"] = "event-cart"
    db = RepairDB(
        {"event-cart:x1": row, "report": _repair_row("report")},
        edges=[
            (
                "report",
                "event-cart:x1",
                {"at": "2026-10-30", "invalidated_by": None},
            )
        ],
        quoted_from_counts={"event-cart:x1": 0},
    )
    repair = QualificationRepair(
        repair_id="reviewed-legacy-repair-v1",
        claims=(reviewed_claim, _repair_claim("report", "report cadence")),
        invalidated_edges=(
            QualificationEdgeRepair("report", "event-cart:x1", "2026-10-30"),
        ),
    )

    with pytest.raises(GraphIntegrityError, match="digest mismatch"):
        GraphWriter(db).repair_qualifications(repair, dry_run=False)

    assert db.writes == []


@pytest.mark.parametrize(
    ("qualifier", "marker", "message"),
    [
        ("cart access", None, "already has qualifier"),
        (None, "another-repair", "another repair"),
    ],
)
def test_repair_rejects_nonlegacy_qualifier_or_foreign_marker(
    qualifier, marker, message
):
    repair = _repair()
    row = _repair_row("cart", qualifier)
    row["qualified_by_repair"] = marker
    db = RepairDB(
        {"cart": row, "report": _repair_row("report")},
        edges=[
            (
                "report",
                "cart",
                {"at": "2026-10-30", "invalidated_by": None},
            )
        ],
    )

    with pytest.raises(GraphIntegrityError, match=message):
        GraphWriter(db).repair_qualifications(repair, dry_run=False)

    assert db.writes == []


@pytest.mark.parametrize("marker", ["", " "])
def test_repair_rejects_empty_edge_invalidation_marker(marker):
    db = RepairDB(
        {"cart": _repair_row("cart"), "report": _repair_row("report")},
        edges=[("report", "cart", {"at": "2026-10-30", "invalidated_by": marker})],
    )
    with pytest.raises(GraphIntegrityError, match="malformed invalidated_by"):
        GraphWriter(db).repair_qualifications(_repair(), dry_run=False)
    assert db.writes == []
