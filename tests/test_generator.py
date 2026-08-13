import json

from trustgraph.generate.generator import BASE_DATE, expand_scenario, write_dataset
from trustgraph.generate.scenarios import deadline_drift, payments_owner_conflict


def _doc(spec):
    return expand_scenario(spec, seed=42)


def test_deterministic(tmp_path):
    first = write_dataset(tmp_path / "a", seed=42)
    second = write_dataset(tmp_path / "b", seed=42)
    assert [p.name for p in first] == [p.name for p in second]
    for pa, pb in zip(first, second):
        assert pa.read_bytes() == pb.read_bytes()


def test_sessions_and_messages_are_well_formed():
    doc = _doc(payments_owner_conflict())
    assert doc["sessions"], "expected sessions"
    for session in doc["sessions"]:
        ts = [m["ts"] for m in session["messages"]]
        assert ts == sorted(ts), "messages must be chronological"
        for msg in session["messages"]:
            assert {"msg_id", "ts", "author", "source_kind", "channel", "text"} <= msg.keys()


def test_every_claim_quote_is_grounded_in_a_message():
    for spec in (payments_owner_conflict(), deadline_drift()):
        doc = _doc(spec)
        texts = [m["text"] for s in doc["sessions"] for m in s["messages"]]
        for claim in doc["ground_truth"]["claims"]:
            assert claim["msg_id"], f"{claim['key']} not linked to a message"
            assert any(claim["quote"] in t for t in texts)


def test_supersession_closes_validity_window():
    doc = _doc(deadline_drift())
    claims = {c["key"]: c for c in doc["ground_truth"]["claims"]}
    assert claims["dl-1"]["status"] == "superseded"
    assert claims["dl-1"]["valid_to"] == claims["dl-2"]["valid_from"]
    assert claims["dl-2"]["valid_to"] == claims["dl-3"]["valid_from"]
    assert claims["dl-3"]["status"] == "active"
    assert claims["dl-3"]["valid_to"] is None


def test_deadline_chain_values():
    doc = _doc(deadline_drift())
    values = [
        (c["value"], c["valid_from"])
        for c in doc["ground_truth"]["claims"]
        if c["predicate"] == "deadline"
    ]
    values.sort(key=lambda pair: pair[1])
    assert [v for v, _ in values] == ["2026-10-03", "2026-10-10", "2026-10-17"]
    assert values[0][1] == (BASE_DATE.replace(day=5)).isoformat()  # day offset 1


def test_conflict_is_unresolved_and_bidirectional_targets_exist():
    doc = _doc(payments_owner_conflict())
    claims = {c["key"]: c for c in doc["ground_truth"]["claims"]}
    assert claims["pay-own-3"]["contradicts_with"] == ["pay-own-2"]
    assert claims["pay-own-3"]["status"] == "active", "conflict claim must not be superseded"
    assert claims["pay-own-2"]["status"] == "active"


def test_abstention_questions_have_no_gold_claims():
    for spec in (payments_owner_conflict(), deadline_drift()):
        doc = _doc(spec)
        for qa in doc["ground_truth"]["qa"]:
            if qa["qtype"] == "abstention":
                assert qa["answer"] == "ABSTAIN"
                assert qa["gold_claim_keys"] == []
            else:
                keys = {c["key"] for c in doc["ground_truth"]["claims"]}
                assert set(qa["gold_claim_keys"]) <= keys
