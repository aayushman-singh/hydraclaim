from trustgraph.claims import QUESTION_TYPES
from trustgraph.longmemeval import convert_instance, estimate_tokens, sample_instances


def _fixture_instance(question_type="temporal-reasoning"):
    return {
        "question_id": "lme-test-1",
        "question_type": question_type,
        "question": "What was the deadline two weeks ago?",
        "answer": "May 1",
        "question_date": "2023/05/20",
        "haystack_session_ids": ["s1", "s2"],
        "haystack_dates": ["2023/05/01", "2023-05-08"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "Can we move the deadline?"},
                {"role": "assistant", "content": "I think May 1 is realistic."},
            ],
            [
                {"role": "user", "content": "Is May 8 still okay?"},
                {"role": "assistant", "content": "Yes, that still works."},
            ],
        ],
    }


def test_convert_instance_message_shape_and_ids():
    doc = convert_instance(_fixture_instance())
    assert doc["scenario_id"] == "lme_lme-test-1"
    assert doc["description"] == "temporal-reasoning"
    assert doc["entities"] == []
    assert len(doc["sessions"]) == 2

    s1, s2 = doc["sessions"]
    assert s1["session_id"] == "s1"
    assert s2["session_id"] == "s2"
    assert s1["started_at"] == "2023-05-01T09:00:00+00:00"
    assert s2["started_at"] == "2023-05-08T09:00:00+00:00"

    for i, msg in enumerate(s1["messages"]):
        assert msg["msg_id"] == f"s1-m{i:03d}"
        assert msg["source_kind"] == "chat"
        assert msg["channel"] == "longmemeval"
        assert msg["author"] in {"user", "assistant"}

    assert s1["messages"][0]["ts"] == "2023-05-01T09:00:00+00:00"
    assert s1["messages"][1]["ts"] == "2023-05-01T09:02:00+00:00"
    assert s2["messages"][0]["ts"] == "2023-05-08T09:00:00+00:00"
    assert s2["messages"][1]["ts"] == "2023-05-08T09:02:00+00:00"


def test_convert_instance_qa_mapping():
    doc = convert_instance(_fixture_instance("temporal-reasoning"))
    qa = doc["ground_truth"]["qa"][0]
    assert qa["qtype"] == "temporal"
    assert qa["question"] == "What was the deadline two weeks ago?"
    assert qa["answer"] == "May 1"
    assert qa["gold_claim_keys"] == []
    assert doc["ground_truth"]["claims"] == []


def test_all_mapped_qtypes_are_valid_question_types():
    mapping_cases = {
        "multi-session": "multi_session",
        "temporal-reasoning": "temporal",
        "knowledge-update": "knowledge_update",
        "abstention": "abstention",
        "single-session-user": "lookup",
        "single-session-assistant": "lookup",
        "single-session-preference": "lookup",
        "anything-else": "lookup",
    }
    for question_type, expected in mapping_cases.items():
        doc = convert_instance(_fixture_instance(question_type))
        qtype = doc["ground_truth"]["qa"][0]["qtype"]
        assert qtype == expected
        assert qtype in QUESTION_TYPES


def test_estimate_tokens_arithmetic():
    doc = convert_instance(_fixture_instance())
    text_len = sum(
        len(msg["text"])
        for s in doc["sessions"]
        for msg in s["messages"]
    )
    assert estimate_tokens(doc) == text_len // 4
    assert estimate_tokens({"sessions": []}) == 0


def test_sample_instances_is_deterministic():
    instances = [
        {"question_type": "lookup", "id": f"l{i}"} for i in range(6)
    ] + [
        {"question_type": "temporal-reasoning", "id": f"t{i}"} for i in range(6)
    ]
    first = sample_instances(instances, n=5, seed=123, per_type_cap=3)
    second = sample_instances(instances, n=5, seed=123, per_type_cap=3)
    assert [inst["id"] for inst in first] == [inst["id"] for inst in second]


def test_sample_instances_respects_per_type_caps_when_no_refill():
    instances = (
        [{"question_type": "lookup", "id": f"l{i}"} for i in range(6)]
        + [{"question_type": "temporal-reasoning", "id": f"t{i}"} for i in range(6)]
    )
    sampled = sample_instances(instances, n=5, seed=1, per_type_cap=3)
    assert len(sampled) == 5
    by_type = {}
    for inst in sampled:
        by_type.setdefault(inst["question_type"], []).append(inst)
    assert len(by_type["lookup"]) <= 3
    assert len(by_type["temporal-reasoning"]) <= 3


def test_sample_instances_refills_from_leftovers_to_reach_n():
    instances = [
        {"question_type": "lookup", "id": f"l{i}"} for i in range(6)
    ] + [
        {"question_type": "temporal-reasoning", "id": f"t{i}"} for i in range(6)
    ]
    sampled = sample_instances(instances, n=7, seed=1, per_type_cap=3)
    assert len(sampled) == 7


def test_date_parsing_accepts_slashes_and_dashes():
    slash = _fixture_instance()
    slash["haystack_dates"] = ["2023/05/01"]
    slash["haystack_sessions"] = slash["haystack_sessions"][:1]
    slash["haystack_session_ids"] = slash["haystack_session_ids"][:1]

    dash = _fixture_instance()
    dash["haystack_dates"] = ["2023-05-01"]
    dash["haystack_sessions"] = dash["haystack_sessions"][:1]
    dash["haystack_session_ids"] = dash["haystack_session_ids"][:1]

    assert convert_instance(slash)["sessions"][0]["started_at"] == "2023-05-01T09:00:00+00:00"
    assert convert_instance(dash)["sessions"][0]["started_at"] == "2023-05-01T09:00:00+00:00"
