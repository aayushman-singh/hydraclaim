from hydraclaim.reconcile import canonicalize_entity, plan_writes

ROSTER = [
    {"name": "Priya Shah", "type": "person", "aliases": ["Priya", "@priya"]},
    {"name": "payments integration", "type": "system", "aliases": ["payments"]},
]


def _draft(**overrides):
    draft = {
        "subject": "payments integration",
        "predicate": "owned_by",
        "value": "Dario Kim",
        "valid_from": "2026-05-14",
        "quote": "I'm taking over the payments integration",
        "author": "Dario Kim",
        "source_kind": "slack",
        "session_id": "s5",
        "msg_id": "s5-m001",
        "explicitness": 1.0,
        "confidence": 0.95,
        "supersedes": None,
    }
    draft.update(overrides)
    return draft


def _active(**overrides):
    claim = {
        "id": "scen:c1",
        "subject": "payments integration",
        "predicate": "owned_by",
        "value": "Priya Shah",
        "valid_from": "2026-05-04",
        "source_kind": "meeting",
        "author": "Meeting notes",
    }
    claim.update(overrides)
    return claim


def test_canonicalize_via_alias_and_passthrough():
    assert canonicalize_entity("@priya", ROSTER) == "Priya Shah"
    assert canonicalize_entity("Payments", ROSTER) == "payments integration"
    assert canonicalize_entity("Unknown Entity", ROSTER) == "Unknown Entity"


def test_rule1_explicit_supersession_closes_target():
    plan = plan_writes([_draft(supersedes="scen:c1")], [_active()], ROSTER)
    assert plan["supersede"] == [
        {"new_id": "draft:x1", "old_id": "scen:c1", "at": "2026-05-14"}
    ]
    assert plan["contradict"] == []
    assert len(plan["create"]) == 1


def test_rule1_dangling_target_warns_and_ingests_plain():
    plan = plan_writes([_draft(supersedes="scen:missing")], [_active()], ROSTER)
    assert plan["supersede"] == []
    # falls through to the cross-source conflict rule (slack vs meeting)
    assert plan["contradict"] == [{"a_id": "draft:x1", "b_id": "scen:c1"}]
    assert len(plan["warnings"]) == 1


def test_rule2_duplicate_is_skipped():
    plan = plan_writes([_draft(value="Priya Shah")], [_active()], ROSTER)
    assert plan["create"] == [] and plan["duplicates"] == 1


def test_rule3_same_source_corrects_itself():
    active = _active(source_kind="slack", author="Mina Okafor", value="2026-10-10",
                     predicate="deadline", subject="product launch")
    draft = _draft(predicate="deadline", subject="product launch", value="2026-10-17")
    plan = plan_writes([draft], [active], ROSTER)
    assert len(plan["supersede"]) == 1
    assert plan["contradict"] == []


def test_rule4_cross_source_conflict_stays_unresolved():
    plan = plan_writes([_draft()], [_active()], ROSTER)
    assert plan["supersede"] == []
    assert plan["contradict"] == [{"a_id": "draft:x1", "b_id": "scen:c1"}]
    assert len(plan["create"]) == 1, "conflicting claims both stay active"


def test_rule4_older_same_source_does_not_supersede():
    active = _active(source_kind="slack", value="2026-10-10", predicate="deadline",
                     subject="product launch")
    draft = _draft(predicate="deadline", subject="product launch", value="2026-10-03",
                   valid_from="2026-05-01")
    plan = plan_writes([draft], [active], ROSTER)
    assert plan["supersede"] == [], "older claim must not supersede a newer one"
    assert plan["contradict"] == [{"a_id": "draft:x1", "b_id": "scen:c1"}]


def test_rule5_plain_new_claim():
    draft = _draft(predicate="works_on", subject="Priya Shah", value="payments integration")
    plan = plan_writes([draft], [_active()], ROSTER)
    assert plan["create"][0]["id"] == "draft:x1"
    assert plan["supersede"] == [] and plan["contradict"] == []


def test_supplied_ids_are_honored():
    plan = plan_writes([_draft(id="scen:x9", supersedes="scen:c1")], [_active()], ROSTER)
    assert plan["supersede"][0]["new_id"] == "scen:x9"
