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
             "rubric": ["2026-05-05", "meeting"],
             "qtype": "lookup",
             "gold_claim_keys": ["dl-1"]},
            {"question": "What budget was approved for launch marketing?",
             "answer": "ABSTAIN",
             "qtype": "abstention",
             "gold_claim_keys": []},
        ],
    }


def feature_owner_handoff() -> dict:
    """Clean handoff in Slack; Linear ticket updated to match."""
    return {
        "scenario_id": "feature_owner_handoff",
        "description": "API gateway ownership moves from Alex to Jordan; both sources agree.",
        "entities": [
            {"name": "api gateway", "type": "system",
             "aliases": ["gateway", "api proxy"]},
            {"name": "Alex Rivera", "type": "person", "aliases": ["Alex", "@alex"]},
            {"name": "Jordan Lee", "type": "person", "aliases": ["Jordan", "@jordan"]},
        ],
        "events": [
            _ev(0, "s1", "meeting", "Meeting notes", "fireflies",
                "Alex Rivera will own the api gateway redesign for Q3.",
                [_claim("gw-own-1", "api gateway", "owned_by", "Alex Rivera", 0,
                        "Alex Rivera will own the api gateway redesign", "Meeting notes", "meeting")]),
            _ev(5, "s2", "slack", "Jordan Lee", "#platform",
                "Taking over the api gateway redesign from Alex — starting the auth refactor today.",
                [_claim("gw-own-2", "api gateway", "owned_by", "Jordan Lee", 5,
                        "Taking over the api gateway redesign from Alex", "Jordan Lee", "slack",
                        supersedes="gw-own-1")]),
            _ev(6, "s3", "linear", "Linear", "linear",
                "API-44 owner updated to Jordan Lee.",
                [_claim("gw-own-3", "api gateway", "owned_by", "Jordan Lee", 6,
                        "owner updated to Jordan Lee", "Linear", "linear")]),
        ],
        "qa": [
            {"question": "Who owns the api gateway redesign?",
             "answer": "Jordan Lee",
             "qtype": "knowledge_update",
             "gold_claim_keys": ["gw-own-2", "gw-own-3"]},
            {"question": "Who originally owned the api gateway redesign?",
             "answer": "Alex Rivera",
             "qtype": "temporal",
             "gold_claim_keys": ["gw-own-1"]},
            {"question": "What is the api gateway's uptime target?",
             "answer": "ABSTAIN",
             "qtype": "abstention",
             "gold_claim_keys": []},
        ],
    }


def status_escalation() -> dict:
    """Project status moves from on track to at risk to blocked."""
    return {
        "scenario_id": "status_escalation",
        "description": "Mobile app launch status degrades across three check-ins.",
        "entities": [
            {"name": "mobile app launch", "type": "project",
             "aliases": ["mobile launch", "app launch"]},
            {"name": "Sam Taylor", "type": "person", "aliases": ["Sam", "@sam"]},
        ],
        "events": [
            _ev(1, "s1", "meeting", "Meeting notes", "fireflies",
                "Mobile app launch is on track for the end of the month.",
                [_claim("mob-status-1", "mobile app launch", "status", "on track", 1,
                        "Mobile app launch is on track", "Meeting notes", "meeting")]),
            _ev(8, "s2", "slack", "Sam Taylor", "#mobile",
                "Heads up: mobile app launch is now at risk because the review cycle is taking longer than expected.",
                [_claim("mob-status-2", "mobile app launch", "status", "at risk", 8,
                        "mobile app launch is now at risk", "Sam Taylor", "slack",
                        supersedes="mob-status-1")]),
            _ev(13, "s3", "linear", "Linear", "linear",
                "MOBILE-12 status changed to Blocked — waiting on App Store review.",
                [_claim("mob-status-3", "mobile app launch", "status", "blocked", 13,
                        "status changed to Blocked", "Linear", "linear",
                        supersedes="mob-status-2")]),
        ],
        "qa": [
            {"question": "What is the current status of the mobile app launch?",
             "answer": "blocked",
             "qtype": "knowledge_update",
             "gold_claim_keys": ["mob-status-3"]},
            {"question": "When did the mobile app launch become at risk?",
             "answer": "2026-05-12",
             "qtype": "lookup",
             "gold_claim_keys": ["mob-status-2"]},
            {"question": "Who is blocked by the App Store review?",
             "answer": "ABSTAIN",
             "qtype": "abstention",
             "gold_claim_keys": []},
        ],
    }


def budget_decision_conflict() -> dict:
    """Approved budget later challenged; finance record differs from project lead."""
    return {
        "scenario_id": "budget_decision_conflict",
        "description": "Marketing budget approved, then cut in Slack; finance system still shows the approved amount.",
        "entities": [
            {"name": "Q3 marketing budget", "type": "project",
             "aliases": ["marketing budget", "q3 budget"]},
            {"name": "Riley Chen", "type": "person", "aliases": ["Riley", "@riley"]},
            {"name": "Finance Bot", "type": "system", "aliases": ["finance"]},
        ],
        "events": [
            _ev(2, "s1", "meeting", "Meeting notes", "fireflies",
                "Q3 marketing budget approved at $120k.",
                [_claim("bud-1", "Q3 marketing budget", "budget", "$120,000", 2,
                        "Q3 marketing budget approved at $120k", "Meeting notes", "meeting")]),
            _ev(9, "s2", "slack", "Riley Chen", "#marketing",
                "Correction: we need to cut the Q3 marketing budget to $80k effective immediately.",
                [_claim("bud-2", "Q3 marketing budget", "budget", "$80,000", 9,
                        "cut the Q3 marketing budget to $80k", "Riley Chen", "slack",
                        supersedes="bud-1")]),
            _ev(11, "s3", "linear", "Finance Bot", "finance",
                "Budget request BUD-77 approved: Q3 marketing budget $120,000.",
                [_claim("bud-3", "Q3 marketing budget", "budget", "$120,000", 11,
                        "Q3 marketing budget $120,000", "Finance Bot", "linear",
                        contradicts_with=["bud-2"])]),
        ],
        "qa": [
            {"question": "What is the current Q3 marketing budget?",
             "answer": ("Unresolved conflict: Riley Chen cut the budget to $80,000 on 2026-05-13, "
                        "but Finance Bot still shows the approved $120,000 on 2026-05-15."),
             "rubric": ["conflict", "$80,000", "$120,000"],
             "qtype": "conflict",
             "gold_claim_keys": ["bud-2", "bud-3"]},
            {"question": "What was the Q3 marketing budget before the cut?",
             "answer": "$120,000",
             "qtype": "temporal",
             "gold_claim_keys": ["bud-1"]},
            {"question": "Which vendor was selected for the Q3 marketing campaign?",
             "answer": "ABSTAIN",
             "qtype": "abstention",
             "gold_claim_keys": []},
        ],
    }


def team_assignment_change() -> dict:
    """Person moved from one team to another."""
    return {
        "scenario_id": "team_assignment_change",
        "description": "Morgan moves from Growth to Platform; org chart updated late.",
        "entities": [
            {"name": "Morgan Patel", "type": "person", "aliases": ["Morgan", "@morgan"]},
            {"name": "Growth team", "type": "team", "aliases": ["Growth"]},
            {"name": "Platform team", "type": "team", "aliases": ["Platform"]},
        ],
        "events": [
            _ev(0, "s1", "meeting", "Meeting notes", "fireflies",
                "Morgan Patel is joining the Growth team next quarter.",
                [_claim("team-1", "Morgan Patel", "assigned_to", "Growth team", 0,
                        "Morgan Patel is joining the Growth team", "Meeting notes", "meeting")]),
            _ev(7, "s2", "slack", "Morgan Patel", "#general",
                "Switching to the Platform team this week — excited to work on infra.",
                [_claim("team-2", "Morgan Patel", "assigned_to", "Platform team", 7,
                        "Switching to the Platform team", "Morgan Patel", "slack",
                        supersedes="team-1")]),
            _ev(12, "s3", "linear", "Linear", "linear",
                "ORG-9 team assignment updated: Morgan Patel -> Platform team.",
                [_claim("team-3", "Morgan Patel", "assigned_to", "Platform team", 12,
                        "Morgan Patel -> Platform team", "Linear", "linear")]),
        ],
        "qa": [
            {"question": "Which team is Morgan Patel on now?",
             "answer": "Platform team",
             "qtype": "knowledge_update",
             "gold_claim_keys": ["team-2", "team-3"]},
            {"question": "Which team was Morgan Patel originally joining?",
             "answer": "Growth team",
             "qtype": "temporal",
             "gold_claim_keys": ["team-1"]},
            {"question": "What is Morgan Patel's salary band?",
             "answer": "ABSTAIN",
             "qtype": "abstention",
             "gold_claim_keys": []},
        ],
    }


def location_change() -> dict:
    """Employee relocates; HR record and Slack message agree."""
    return {
        "scenario_id": "location_change",
        "description": "Casey moves from San Francisco to New York.",
        "entities": [
            {"name": "Casey Brooks", "type": "person", "aliases": ["Casey", "@casey"]},
        ],
        "events": [
            _ev(0, "s1", "meeting", "Meeting notes", "fireflies",
                "Casey Brooks is based in San Francisco.",
                [_claim("loc-1", "Casey Brooks", "located_in", "San Francisco", 0,
                        "Casey Brooks is based in San Francisco", "Meeting notes", "meeting")]),
            _ev(10, "s2", "slack", "Casey Brooks", "#general",
                "Officially moved to New York as of this week.",
                [_claim("loc-2", "Casey Brooks", "located_in", "New York", 10,
                        "Officially moved to New York", "Casey Brooks", "slack",
                        supersedes="loc-1")]),
            _ev(11, "s3", "linear", "HR Bot", "hr",
                "HR-88 location updated: Casey Brooks -> New York.",
                [_claim("loc-3", "Casey Brooks", "located_in", "New York", 11,
                        "Casey Brooks -> New York", "HR Bot", "linear")]),
        ],
        "qa": [
            {"question": "Where is Casey Brooks located now?",
             "answer": "New York",
             "qtype": "knowledge_update",
             "gold_claim_keys": ["loc-2", "loc-3"]},
            {"question": "Where was Casey Brooks based originally?",
             "answer": "San Francisco",
             "qtype": "temporal",
             "gold_claim_keys": ["loc-1"]},
            {"question": "What is Casey Brooks's phone number?",
             "answer": "ABSTAIN",
             "qtype": "abstention",
             "gold_claim_keys": []},
        ],
    }


def dependency_unblocked() -> dict:
    """One service blocks another, then the blocker is resolved."""
    return {
        "scenario_id": "dependency_unblocked",
        "description": "Checkout rollout blocked by search outage, then unblocked.",
        "entities": [
            {"name": "checkout rollout", "type": "project", "aliases": ["checkout"]},
            {"name": "search service", "type": "system", "aliases": ["search"]},
        ],
        "events": [
            _ev(3, "s1", "slack", "Engineering lead", "#eng",
                "Checkout rollout status: blocked by the search service outage.",
                [_claim("dep-1", "checkout rollout", "status", "blocked by search service", 3,
                        "Checkout rollout status: blocked by the search service",
                        "Engineering lead", "slack")]),
            _ev(8, "s2", "slack", "Engineering lead", "#eng",
                "Search service is back online — checkout rollout is now unblocked.",
                [_claim("dep-2", "checkout rollout", "status", "unblocked", 8,
                        "checkout rollout is now unblocked", "Engineering lead", "slack",
                        supersedes="dep-1")]),
        ],
        "qa": [
            {"question": "What is the current status of the checkout rollout?",
             "answer": "unblocked",
             "qtype": "knowledge_update",
             "gold_claim_keys": ["dep-2"]},
            {"question": "What was blocking the checkout rollout?",
             "answer": "search service outage",
             "qtype": "temporal",
             "gold_claim_keys": ["dep-1"]},
            {"question": "Who owns the checkout rollout?",
             "answer": "ABSTAIN",
             "qtype": "abstention",
             "gold_claim_keys": []},
        ],
    }


SCENARIOS = [
    payments_owner_conflict,
    deadline_drift,
    feature_owner_handoff,
    status_escalation,
    budget_decision_conflict,
    team_assignment_change,
    location_change,
    dependency_unblocked,
]
