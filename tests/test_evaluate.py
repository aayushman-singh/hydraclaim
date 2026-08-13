from trustgraph.evaluate import evaluate

SCENARIO = {
    "scenario_id": "mini",
    "entities": [{"name": "product launch", "type": "project", "aliases": ["the launch"]}],
    "ground_truth": {
        "claims": [
            {"key": "dl-1", "subject": "product launch", "predicate": "deadline",
             "value": "2026-10-03", "valid_from": "2026-05-05", "supersedes": None},
            {"key": "dl-2", "subject": "product launch", "predicate": "deadline",
             "value": "2026-10-10", "valid_from": "2026-05-10", "supersedes": "dl-1"},
            {"key": "dl-3", "subject": "product launch", "predicate": "deadline",
             "value": "2026-10-17", "valid_from": "2026-05-18", "supersedes": "dl-2"},
            {"key": "st-1", "subject": "product launch", "predicate": "status",
             "value": "In Progress", "valid_from": "2026-05-19", "supersedes": None},
        ],
        "qa": [],
    },
}


def _draft(key, gold_key, supersedes=None):
    gold = {c["key"]: c for c in SCENARIO["ground_truth"]["claims"]}[gold_key]
    return {
        "id": f"mini:{key}",
        "subject": gold["subject"],
        "predicate": gold["predicate"],
        "value": gold["value"],
        "valid_from": gold["valid_from"],
        "supersedes": supersedes,
    }


def test_perfect_extraction():
    drafts = [
        _draft("x1", "dl-1"),
        _draft("x2", "dl-2", supersedes="mini:x1"),
        _draft("x3", "dl-3", supersedes="mini:x2"),
        _draft("x4", "st-1"),
    ]
    report = evaluate(drafts, SCENARIO)
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0
    assert report["f1"] == 1.0
    assert report["supersession_recall"] == 1.0


def test_missing_gold_drops_recall():
    drafts = [_draft("x1", "dl-1"), _draft("x2", "dl-2", supersedes="mini:x1"),
              _draft("x3", "dl-3", supersedes="mini:x2")]
    report = evaluate(drafts, SCENARIO)
    assert report["recall"] == 0.75
    assert report["precision"] == 1.0
    assert report["unmatched_gold"] == ["st-1"]


def test_spurious_draft_drops_precision():
    spurious = {"id": "mini:x9", "subject": "product launch", "predicate": "budget",
                "value": "$5000", "valid_from": "2026-05-10", "supersedes": None}
    drafts = [_draft("x1", "dl-1"), _draft("x2", "dl-2", supersedes="mini:x1"),
              _draft("x3", "dl-3", supersedes="mini:x2"), _draft("x4", "st-1"), spurious]
    report = evaluate(drafts, SCENARIO)
    assert report["precision"] == 0.8
    assert report["recall"] == 1.0
    assert report["spurious"] == ["product launch | budget | $5000"]


def test_value_normalization_matches():
    draft = {"id": "mini:x1", "subject": "The Launch", "predicate": "deadline",
             "value": "  2026-10-03 ", "valid_from": "2026-05-05", "supersedes": None}
    report = evaluate([draft], SCENARIO)
    assert report["tp"] == 1, "alias subject + padded value must still match"


def test_duplicate_draft_counted_not_matched_twice():
    drafts = [_draft("x1", "dl-1"), _draft("x2", "dl-1")]
    report = evaluate(drafts, SCENARIO)
    assert report["tp"] == 1
    assert report["duplicates"] == 1
    assert report["recall"] == 0.25
