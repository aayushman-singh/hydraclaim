from datetime import datetime, timezone

from hydraclaim.scoring import (
    author_authority,
    authority,
    rank_claims,
    recency_decay,
    score_claim,
)

NOW = datetime(2026, 5, 25, tzinfo=timezone.utc)


def test_authority_predicate_overrides_kind_default():
    assert authority("linear", "status") == 0.9      # override
    assert authority("linear", "deadline") == 0.4    # override
    assert authority("linear", "works_on") == 0.7    # kind default
    assert authority("slack", "status") == 0.5       # kind default
    assert authority("carrier-pigeon", "status") == 0.4


def test_recency_decay_half_life_and_floor():
    assert recency_decay("2026-05-25", NOW) == 1.0
    assert recency_decay("2026-04-25", NOW) == 0.5          # one half-life
    assert recency_decay("2026-03-26", NOW) == 0.25         # two half-lives
    assert recency_decay("2020-01-01", NOW) == 0.05         # floor
    assert recency_decay("2027-01-01", NOW) == 1.0          # future dates clamp


def test_author_authority_self_announcement_bonus():
    base = author_authority("Mina Okafor", "slack", "2026-10-10")
    boosted = author_authority("Dario Kim", "slack", "Dario Kim")
    assert boosted > base
    assert author_authority("Linear", "linear", "Priya Shah") == 0.9


def test_score_weights_line_up():
    claim = {
        "valid_from": "2026-05-25",
        "source_kind": "meeting",
        "author": "Meeting notes",
        "value": "2026-10-17",
        "explicitness": 1.0,
        "extraction_confidence": 0.9,
    }
    expected = 0.35 * 0.8 + 0.20 * 1.0 + 0.20 * 0.85 + 0.15 * 1.0 + 0.10 * 0.9
    assert abs(score_claim(claim, "deadline", NOW) - expected) < 1e-9


def test_rank_claims_prefers_recent_self_announced_over_stale_record():
    stale_linear = {
        "id": "c3", "value": "Priya Shah", "valid_from": "2026-05-21",
        "source_kind": "linear", "author": "Linear",
        "explicitness": 1.0, "extraction_confidence": 0.95,
    }
    fresh_slack = {
        "id": "c2", "value": "Dario Kim", "valid_from": "2026-05-14",
        "source_kind": "slack", "author": "Dario Kim",
        "explicitness": 1.0, "extraction_confidence": 0.95,
    }
    ranked = rank_claims([fresh_slack, stale_linear], "owned_by", NOW)
    # Both defensible; the test pins the CURRENT weights so a retune is a
    # deliberate act, not an accident.
    assert [c["id"] for c, _ in ranked] == ["c3", "c2"]
    assert ranked[0][1] > ranked[1][1]
