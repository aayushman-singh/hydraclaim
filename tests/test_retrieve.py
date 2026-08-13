from trustgraph.retrieve import (
    abstain_message,
    build_chain_answer,
    build_conflict_answer,
    build_fast_answer,
)

CLAIM = {
    "id": "deadline_drift:dl-3",
    "subject": "product launch",
    "predicate": "deadline",
    "value": "2026-10-17",
    "valid_from": "2026-05-18",
    "valid_to": None,
    "status": "active",
    "source_kind": "meeting",
    "author": "Meeting notes",
    "quote": "the launch deadline locks at October 17",
}


def test_fast_answer_cites_value_source_and_quote():
    text = build_fast_answer(CLAIM)
    assert "2026-10-17" in text
    assert "meeting/Meeting notes" in text
    assert "the launch deadline locks at October 17" in text


def test_chain_answer_lists_history_oldest_behind_current():
    chain = [
        {"id": "dl-2", "value": "2026-10-10", "valid_from": "2026-05-10",
         "valid_to": "2026-05-18", "hops": 1},
        {"id": "dl-1", "value": "2026-10-03", "valid_from": "2026-05-05",
         "valid_to": "2026-05-10", "hops": 2},
    ]
    text = build_chain_answer(CLAIM, chain)
    assert "current, since 2026-05-18" in text
    assert "2026-10-10 (2026-05-10 -> 2026-05-18)" in text
    assert "2026-10-03" in text


def test_conflict_answer_shows_all_sides_and_winner():
    ranked = [
        ({**CLAIM, "id": "c3", "predicate": "owned_by", "value": "Priya Shah",
          "subject": "payments integration", "source_kind": "linear", "author": "Linear",
          "quote": "owner set to Priya Shah", "valid_from": "2026-05-21"}, 0.71),
        ({**CLAIM, "id": "c2", "predicate": "owned_by", "value": "Dario Kim",
          "subject": "payments integration", "source_kind": "slack",
          "author": "Dario Kim", "quote": "taking over the payments integration",
          "valid_from": "2026-05-14"}, 0.63),
    ]
    text = build_conflict_answer("payments integration", "owned_by", ranked)
    assert "Unresolved conflict" in text
    assert "Priya Shah" in text and "Dario Kim" in text
    assert "0.71" in text
    assert text.index("Priya Shah") < text.index("Dario Kim")  # winner first
    assert "never reconciled" in text


def test_abstain_message_names_what_was_searched():
    text = abstain_message("product launch", "budget")
    assert "budget" in text and "product launch" in text
    assert "not in the history" in text
    assert "searched" in abstain_message("coffee machine", None)
