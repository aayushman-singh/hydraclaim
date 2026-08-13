"""Scripted scenarios: overwrites, cross-source contradictions, abstention probes.

Dates below are day offsets from the generator base date (2026-05-04), so
absolute dates in QA answers are derived, not hard-coded — check the expansion
in generator.py before adding new answer strings.
"""

from __future__ import annotations


def _ev(day, session, source_kind, author, channel, text, claims=()):
    return {
        "day": day,
        "session": session,
        "source_kind": source_kind,
        "author": author,
        "channel": channel,
        "text": text,
        "claims": list(claims),
    }


def _claim(key, subject, predicate, value, day, quote, author, source_kind,
           explicitness=1.0, confidence=0.95, supersedes=None, contradicts_with=()):
    return {
        "key": key,
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "day": day,
        "quote": quote,
        "author": author,
        "source_kind": source_kind,
        "explicitness": explicitness,
        "confidence": confidence,
        "supersedes": supersedes,
        "contradicts_with": list(contradicts_with),
    }


def payments_owner_conflict() -> dict:
    """Slack handoff announcement vs. a Linear record nobody updated."""
    return {
        "scenario_id": "payments_owner_conflict",
        "description": "Handoff recorded in Slack; Linear still lists the old owner — unresolved conflict.",
        "entities": [
            {"name": "payments integration", "type": "system",
             "aliases": ["payments", "checkout service"]},
            {"name": "Priya Shah", "type": "person", "aliases": ["Priya", "@priya"]},
            {"name": "Dario Kim", "type": "person", "aliases": ["Dario", "@dario"]},
        ],
        "events": [
            _ev(0, "s1", "meeting", "Meeting notes", "fireflies",
                "Meeting decision: Priya Shah owns the payments integration end to end.",
                [_claim("pay-own-1", "payments integration", "owned_by", "Priya Shah", 0,
                        "Priya Shah owns the payments integration end to end",
                        "Meeting notes", "meeting")]),
            _ev(3, "s2", "slack", "Priya Shah", "#payments",
                "Kicking off the payments integration this week — first design review is on Thursday.",
                [_claim("pay-work-1", "Priya Shah", "works_on", "payments integration", 3,
                        "Kicking off the payments integration this week",
                        "Priya Shah", "slack", explicitness=0.9, confidence=0.9)]),
            _ev(10, "s5", "slack", "Dario Kim", "#eng",
                "Correction to earlier plans: I'm taking over the payments integration from Priya, effective today.",
                [_claim("pay-own-2", "payments integration", "owned_by", "Dario Kim", 10,
                        "I'm taking over the payments integration from Priya",
                        "Dario Kim", "slack", supersedes="pay-own-1")]),
            _ev(17, "s7", "linear", "Linear", "linear",
                "PAY-118 'Payments integration rollout' owner set to Priya Shah.",
                [_claim("pay-own-3", "payments integration", "owned_by", "Priya Shah", 17,
                        "owner set to Priya Shah", "Linear", "linear",
                        contradicts_with=["pay-own-2"])]),
            _ev(20, "s8", "meeting", "Meeting notes", "fireflies",
                "Roadmap review: the payments integration ships as part of the October launch.",
                [_claim("pay-ship-1", "payments integration", "status",
                        "ships with October launch", 20,
                        "ships as part of the October launch",
                        "Meeting notes", "meeting", explicitness=0.8, confidence=0.85)]),
        ],
        "qa": [
            {"question": "Who owns the payments integration?",
             "answer": ("Unresolved conflict: Dario Kim announced a handoff from Priya Shah "
                        "on 2026-05-14, but Linear (2026-05-21) still lists Priya Shah. The "
                        "latest explicit handoff says Dario Kim; the Linear record needs "
                        "reconciliation."),
             "rubric": ["unresolved conflict", "Dario Kim", "Priya Shah"],
             "qtype": "conflict",
             "gold_claim_keys": ["pay-own-2", "pay-own-3"]},
            {"question": "Who owned the payments integration at the start of May?",
             "answer": "Priya Shah",
             "qtype": "temporal",
             "gold_claim_keys": ["pay-own-1"]},
            {"question": "What is the payments integration's uptime SLA?",
             "answer": "ABSTAIN",
             "qtype": "abstention",
             "gold_claim_keys": []},
        ],
    }


def deadline_drift() -> dict:
    """One fact overwritten twice across sources — the chronology demo."""
    return {
        "scenario_id": "deadline_drift",
        "description": "Launch deadline moved twice (meeting -> slack -> meeting); supersession chain of depth 2.",
        "entities": [
            {"name": "product launch", "type": "project",
             "aliases": ["launch", "the launch", "October launch"]},
            {"name": "Mina Okafor", "type": "person", "aliases": ["Mina", "@mina"]},
        ],
        "events": [
            _ev(1, "s1", "meeting", "Meeting notes", "fireflies",
                "Launch planning: the product launch deadline is October 3.",
                [_claim("dl-1", "product launch", "deadline", "2026-10-03", 1,
                        "the product launch deadline is October 3",
                        "Meeting notes", "meeting")]),
            _ev(2, "s2", "slack", "Mina Okafor", "#launch",
                "Draft launch checklist is up — still aiming for the October 3 date we set yesterday."),
            _ev(6, "s3", "slack", "Mina Okafor", "#launch",
                "Heads up: moving the launch deadline to October 10 — we need the extra week for payments.",
                [_claim("dl-2", "product launch", "deadline", "2026-10-10", 6,
                        "moving the launch deadline to October 10",
                        "Mina Okafor", "slack", supersedes="dl-1")]),
            _ev(14, "s6", "meeting", "Meeting notes", "fireflies",
                "Final call in roadmap review: the launch deadline locks at October 17.",
                [_claim("dl-3", "product launch", "deadline", "2026-10-17", 14,
                        "the launch deadline locks at October 17",
                        "Meeting notes", "meeting", supersedes="dl-2")]),
            _ev(15, "s7", "linear", "Linear", "linear",
                "LAUNCH-7 'Launch readiness' status changed to In Progress.",
                [_claim("ln-status-1", "product launch", "status", "In Progress", 15,
                        "status changed to In Progress", "Linear", "linear")]),
        ],
        "qa": [
            {"question": "What is the current launch deadline?",
             "answer": "2026-10-17",
             "qtype": "knowledge_update",
             "gold_claim_keys": ["dl-3"]},
            {"question": "What was the launch deadline before the most recent change?",
             "answer": "2026-10-10",
             "qtype": "temporal",
             "gold_claim_keys": ["dl-2"]},
            {"question": "When was the launch deadline first set?",
             "answer": "2026-05-05, in the launch planning meeting (session s1)",
             "qtype": "lookup",
             "gold_claim_keys": ["dl-1"]},
            {"question": "What budget was approved for launch marketing?",
             "answer": "ABSTAIN",
             "qtype": "abstention",
             "gold_claim_keys": []},
        ],
    }


SCENARIOS = [payments_owner_conflict, deadline_drift]
