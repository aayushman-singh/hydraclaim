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


def _claim(
    key,
    subject,
    predicate,
    value,
    day,
    quote,
    author,
    source_kind,
    explicitness=1.0,
    confidence=0.95,
    supersedes=None,
    contradicts_with=(),
):
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
            {
                "name": "payments integration",
                "type": "system",
                "aliases": ["payments", "checkout service"],
            },
            {"name": "Priya Shah", "type": "person", "aliases": ["Priya", "@priya"]},
            {"name": "Dario Kim", "type": "person", "aliases": ["Dario", "@dario"]},
        ],
        "events": [
            _ev(
                0,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Meeting decision: Priya Shah owns the payments integration end to end.",
                [
                    _claim(
                        "pay-own-1",
                        "payments integration",
                        "owned_by",
                        "Priya Shah",
                        0,
                        "Priya Shah owns the payments integration end to end",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                3,
                "s2",
                "slack",
                "Priya Shah",
                "#payments",
                "Kicking off the payments integration this week — first design review is on Thursday.",
                [
                    _claim(
                        "pay-work-1",
                        "Priya Shah",
                        "works_on",
                        "payments integration",
                        3,
                        "Kicking off the payments integration this week",
                        "Priya Shah",
                        "slack",
                        explicitness=0.9,
                        confidence=0.9,
                    )
                ],
            ),
            _ev(
                10,
                "s5",
                "slack",
                "Dario Kim",
                "#eng",
                "Correction to earlier plans: I'm taking over the payments integration from Priya, effective today.",
                [
                    _claim(
                        "pay-own-2",
                        "payments integration",
                        "owned_by",
                        "Dario Kim",
                        10,
                        "I'm taking over the payments integration from Priya",
                        "Dario Kim",
                        "slack",
                        supersedes="pay-own-1",
                    )
                ],
            ),
            _ev(
                17,
                "s7",
                "linear",
                "Linear",
                "linear",
                "PAY-118 'Payments integration rollout' owner set to Priya Shah.",
                [
                    _claim(
                        "pay-own-3",
                        "payments integration",
                        "owned_by",
                        "Priya Shah",
                        17,
                        "owner set to Priya Shah",
                        "Linear",
                        "linear",
                        contradicts_with=["pay-own-2"],
                    )
                ],
            ),
            _ev(
                20,
                "s8",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Roadmap review: the payments integration ships as part of the October launch.",
                [
                    _claim(
                        "pay-ship-1",
                        "payments integration",
                        "status",
                        "ships with October launch",
                        20,
                        "ships as part of the October launch",
                        "Meeting notes",
                        "meeting",
                        explicitness=0.8,
                        confidence=0.85,
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "Who owns the payments integration?",
                "answer": (
                    "Unresolved conflict: Dario Kim announced a handoff from Priya Shah "
                    "on 2026-05-14, but Linear (2026-05-21) still lists Priya Shah. The "
                    "latest explicit handoff says Dario Kim; the Linear record needs "
                    "reconciliation."
                ),
                "rubric": ["unresolved conflict", "Dario Kim", "Priya Shah"],
                "qtype": "conflict",
                "gold_claim_keys": ["pay-own-2", "pay-own-3"],
            },
            {
                "question": "Who owned the payments integration at the start of May?",
                "answer": "Priya Shah",
                "qtype": "temporal",
                "gold_claim_keys": ["pay-own-1"],
            },
            {
                "question": "What is the payments integration's uptime SLA?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def deadline_drift() -> dict:
    """One fact overwritten twice across sources — the chronology demo."""
    return {
        "scenario_id": "deadline_drift",
        "description": "Launch deadline moved twice (meeting -> slack -> meeting); supersession chain of depth 2.",
        "entities": [
            {
                "name": "product launch",
                "type": "project",
                "aliases": ["launch", "the launch", "October launch"],
            },
            {"name": "Mina Okafor", "type": "person", "aliases": ["Mina", "@mina"]},
        ],
        "events": [
            _ev(
                1,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Launch planning: the product launch deadline is October 3.",
                [
                    _claim(
                        "dl-1",
                        "product launch",
                        "deadline",
                        "2026-10-03",
                        1,
                        "the product launch deadline is October 3",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                2,
                "s2",
                "slack",
                "Mina Okafor",
                "#launch",
                "Draft launch checklist is up — still aiming for the October 3 date we set yesterday.",
            ),
            _ev(
                6,
                "s3",
                "slack",
                "Mina Okafor",
                "#launch",
                "Heads up: moving the launch deadline to October 10 — we need the extra week for payments.",
                [
                    _claim(
                        "dl-2",
                        "product launch",
                        "deadline",
                        "2026-10-10",
                        6,
                        "moving the launch deadline to October 10",
                        "Mina Okafor",
                        "slack",
                        supersedes="dl-1",
                    )
                ],
            ),
            _ev(
                14,
                "s6",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Final call in roadmap review: the launch deadline locks at October 17.",
                [
                    _claim(
                        "dl-3",
                        "product launch",
                        "deadline",
                        "2026-10-17",
                        14,
                        "the launch deadline locks at October 17",
                        "Meeting notes",
                        "meeting",
                        supersedes="dl-2",
                    )
                ],
            ),
            _ev(
                15,
                "s7",
                "linear",
                "Linear",
                "linear",
                "LAUNCH-7 'Launch readiness' status changed to In Progress.",
                [
                    _claim(
                        "ln-status-1",
                        "product launch",
                        "status",
                        "In Progress",
                        15,
                        "status changed to In Progress",
                        "Linear",
                        "linear",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "What is the current launch deadline?",
                "answer": "2026-10-17",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["dl-3"],
            },
            {
                "question": "What was the launch deadline before the most recent change?",
                "answer": "2026-10-10",
                "qtype": "temporal",
                "gold_claim_keys": ["dl-2"],
            },
            {
                "question": "When was the launch deadline first set?",
                "answer": "2026-05-05, in the launch planning meeting (session s1)",
                "rubric": ["2026-05-05", "meeting"],
                "qtype": "lookup",
                "gold_claim_keys": ["dl-1"],
            },
            {
                "question": "What budget was approved for launch marketing?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def feature_owner_handoff() -> dict:
    """Clean handoff in Slack; Linear ticket updated to match."""
    return {
        "scenario_id": "feature_owner_handoff",
        "description": "API gateway ownership moves from Alex to Jordan; both sources agree.",
        "entities": [
            {
                "name": "api gateway",
                "type": "system",
                "aliases": ["gateway", "api proxy"],
            },
            {"name": "Alex Rivera", "type": "person", "aliases": ["Alex", "@alex"]},
            {"name": "Jordan Lee", "type": "person", "aliases": ["Jordan", "@jordan"]},
        ],
        "events": [
            _ev(
                0,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Alex Rivera will own the api gateway redesign for Q3.",
                [
                    _claim(
                        "gw-own-1",
                        "api gateway",
                        "owned_by",
                        "Alex Rivera",
                        0,
                        "Alex Rivera will own the api gateway redesign",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                5,
                "s2",
                "slack",
                "Jordan Lee",
                "#platform",
                "Taking over the api gateway redesign from Alex — starting the auth refactor today.",
                [
                    _claim(
                        "gw-own-2",
                        "api gateway",
                        "owned_by",
                        "Jordan Lee",
                        5,
                        "Taking over the api gateway redesign from Alex",
                        "Jordan Lee",
                        "slack",
                        supersedes="gw-own-1",
                    )
                ],
            ),
            _ev(
                6,
                "s3",
                "linear",
                "Linear",
                "linear",
                "API-44 owner updated to Jordan Lee.",
                [
                    _claim(
                        "gw-own-3",
                        "api gateway",
                        "owned_by",
                        "Jordan Lee",
                        6,
                        "owner updated to Jordan Lee",
                        "Linear",
                        "linear",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "Who owns the api gateway redesign?",
                "answer": "Jordan Lee",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["gw-own-2", "gw-own-3"],
            },
            {
                "question": "Who originally owned the api gateway redesign?",
                "answer": "Alex Rivera",
                "qtype": "temporal",
                "gold_claim_keys": ["gw-own-1"],
            },
            {
                "question": "What is the api gateway's uptime target?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def status_escalation() -> dict:
    """Project status moves from on track to at risk to blocked."""
    return {
        "scenario_id": "status_escalation",
        "description": "Mobile app launch status degrades across three check-ins.",
        "entities": [
            {
                "name": "mobile app launch",
                "type": "project",
                "aliases": ["mobile launch", "app launch"],
            },
            {"name": "Sam Taylor", "type": "person", "aliases": ["Sam", "@sam"]},
        ],
        "events": [
            _ev(
                1,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Mobile app launch is on track for the end of the month.",
                [
                    _claim(
                        "mob-status-1",
                        "mobile app launch",
                        "status",
                        "on track",
                        1,
                        "Mobile app launch is on track",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                8,
                "s2",
                "slack",
                "Sam Taylor",
                "#mobile",
                "Heads up: mobile app launch is now at risk because the review cycle is taking longer than expected.",
                [
                    _claim(
                        "mob-status-2",
                        "mobile app launch",
                        "status",
                        "at risk",
                        8,
                        "mobile app launch is now at risk",
                        "Sam Taylor",
                        "slack",
                        supersedes="mob-status-1",
                    )
                ],
            ),
            _ev(
                13,
                "s3",
                "linear",
                "Linear",
                "linear",
                "MOBILE-12 status changed to Blocked — waiting on App Store review.",
                [
                    _claim(
                        "mob-status-3",
                        "mobile app launch",
                        "status",
                        "blocked",
                        13,
                        "status changed to Blocked",
                        "Linear",
                        "linear",
                        supersedes="mob-status-2",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "What is the current status of the mobile app launch?",
                "answer": "blocked",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["mob-status-3"],
            },
            {
                "question": "When did the mobile app launch become at risk?",
                "answer": "2026-05-12",
                "qtype": "lookup",
                "gold_claim_keys": ["mob-status-2"],
            },
            {
                "question": "Who is blocked by the App Store review?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def budget_decision_conflict() -> dict:
    """Approved budget later challenged; finance record differs from project lead."""
    return {
        "scenario_id": "budget_decision_conflict",
        "description": "Marketing budget approved, then cut in Slack; finance system still shows the approved amount.",
        "entities": [
            {
                "name": "Q3 marketing budget",
                "type": "project",
                "aliases": ["marketing budget", "q3 budget"],
            },
            {"name": "Riley Chen", "type": "person", "aliases": ["Riley", "@riley"]},
            {"name": "Finance Bot", "type": "system", "aliases": ["finance"]},
        ],
        "events": [
            _ev(
                2,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Q3 marketing budget approved at $120k.",
                [
                    _claim(
                        "bud-1",
                        "Q3 marketing budget",
                        "budget",
                        "$120,000",
                        2,
                        "Q3 marketing budget approved at $120k",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                9,
                "s2",
                "slack",
                "Riley Chen",
                "#marketing",
                "Correction: we need to cut the Q3 marketing budget to $80k effective immediately.",
                [
                    _claim(
                        "bud-2",
                        "Q3 marketing budget",
                        "budget",
                        "$80,000",
                        9,
                        "cut the Q3 marketing budget to $80k",
                        "Riley Chen",
                        "slack",
                        supersedes="bud-1",
                    )
                ],
            ),
            _ev(
                11,
                "s3",
                "linear",
                "Finance Bot",
                "finance",
                "Budget request BUD-77 approved: Q3 marketing budget $120,000.",
                [
                    _claim(
                        "bud-3",
                        "Q3 marketing budget",
                        "budget",
                        "$120,000",
                        11,
                        "Q3 marketing budget $120,000",
                        "Finance Bot",
                        "linear",
                        contradicts_with=["bud-2"],
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "What is the current Q3 marketing budget?",
                "answer": (
                    "Unresolved conflict: Riley Chen cut the budget to $80,000 on 2026-05-13, "
                    "but Finance Bot still shows the approved $120,000 on 2026-05-15."
                ),
                "rubric": ["conflict", "$80,000", "$120,000"],
                "qtype": "conflict",
                "gold_claim_keys": ["bud-2", "bud-3"],
            },
            {
                "question": "What was the Q3 marketing budget before the cut?",
                "answer": "$120,000",
                "qtype": "temporal",
                "gold_claim_keys": ["bud-1"],
            },
            {
                "question": "Which vendor was selected for the Q3 marketing campaign?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def team_assignment_change() -> dict:
    """Person moved from one team to another."""
    return {
        "scenario_id": "team_assignment_change",
        "description": "Morgan moves from Growth to Platform; org chart updated late.",
        "entities": [
            {
                "name": "Morgan Patel",
                "type": "person",
                "aliases": ["Morgan", "@morgan"],
            },
            {"name": "Growth team", "type": "team", "aliases": ["Growth"]},
            {"name": "Platform team", "type": "team", "aliases": ["Platform"]},
        ],
        "events": [
            _ev(
                0,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Morgan Patel is joining the Growth team next quarter.",
                [
                    _claim(
                        "team-1",
                        "Morgan Patel",
                        "assigned_to",
                        "Growth team",
                        0,
                        "Morgan Patel is joining the Growth team",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                7,
                "s2",
                "slack",
                "Morgan Patel",
                "#general",
                "Switching to the Platform team this week — excited to work on infra.",
                [
                    _claim(
                        "team-2",
                        "Morgan Patel",
                        "assigned_to",
                        "Platform team",
                        7,
                        "Switching to the Platform team",
                        "Morgan Patel",
                        "slack",
                        supersedes="team-1",
                    )
                ],
            ),
            _ev(
                12,
                "s3",
                "linear",
                "Linear",
                "linear",
                "ORG-9 team assignment updated: Morgan Patel -> Platform team.",
                [
                    _claim(
                        "team-3",
                        "Morgan Patel",
                        "assigned_to",
                        "Platform team",
                        12,
                        "Morgan Patel -> Platform team",
                        "Linear",
                        "linear",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "Which team is Morgan Patel on now?",
                "answer": "Platform team",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["team-2", "team-3"],
            },
            {
                "question": "Which team was Morgan Patel originally joining?",
                "answer": "Growth team",
                "qtype": "temporal",
                "gold_claim_keys": ["team-1"],
            },
            {
                "question": "What is Morgan Patel's salary band?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
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
            _ev(
                0,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Casey Brooks is based in San Francisco.",
                [
                    _claim(
                        "loc-1",
                        "Casey Brooks",
                        "located_in",
                        "San Francisco",
                        0,
                        "Casey Brooks is based in San Francisco",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                10,
                "s2",
                "slack",
                "Casey Brooks",
                "#general",
                "Officially moved to New York as of this week.",
                [
                    _claim(
                        "loc-2",
                        "Casey Brooks",
                        "located_in",
                        "New York",
                        10,
                        "Officially moved to New York",
                        "Casey Brooks",
                        "slack",
                        supersedes="loc-1",
                    )
                ],
            ),
            _ev(
                11,
                "s3",
                "linear",
                "HR Bot",
                "hr",
                "HR-88 location updated: Casey Brooks -> New York.",
                [
                    _claim(
                        "loc-3",
                        "Casey Brooks",
                        "located_in",
                        "New York",
                        11,
                        "Casey Brooks -> New York",
                        "HR Bot",
                        "linear",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "Where is Casey Brooks located now?",
                "answer": "New York",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["loc-2", "loc-3"],
            },
            {
                "question": "Where was Casey Brooks based originally?",
                "answer": "San Francisco",
                "qtype": "temporal",
                "gold_claim_keys": ["loc-1"],
            },
            {
                "question": "What is Casey Brooks's phone number?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
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
            _ev(
                3,
                "s1",
                "slack",
                "Engineering lead",
                "#eng",
                "Checkout rollout status: blocked by the search service outage.",
                [
                    _claim(
                        "dep-1",
                        "checkout rollout",
                        "status",
                        "blocked by search service outage",
                        3,
                        "Checkout rollout status: blocked by the search service outage",
                        "Engineering lead",
                        "slack",
                    )
                ],
            ),
            _ev(
                8,
                "s2",
                "slack",
                "Engineering lead",
                "#eng",
                "Search service is back online — checkout rollout is now unblocked.",
                [
                    _claim(
                        "dep-2",
                        "checkout rollout",
                        "status",
                        "unblocked",
                        8,
                        "checkout rollout is now unblocked",
                        "Engineering lead",
                        "slack",
                        supersedes="dep-1",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "What is the current status of the checkout rollout?",
                "answer": "unblocked",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["dep-2"],
            },
            {
                "question": "What was blocking the checkout rollout?",
                "answer": "search service outage",
                "qtype": "temporal",
                "gold_claim_keys": ["dep-1"],
            },
            {
                "question": "Who owns the checkout rollout?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def deep_supersession_chain() -> dict:
    """A price/deadline overwritten four times — deep chain + origin lookup."""
    return {
        "scenario_id": "deep_supersession_chain",
        "description": "A server budget is revised four times; the earliest commit is recoverable.",
        "entities": [
            {
                "name": "infra refresh",
                "type": "project",
                "aliases": ["infra", "refresh", "infrastructure upgrade"],
            },
            {"name": "DevOps Bot", "type": "system", "aliases": ["devops"]},
        ],
        "events": [
            _ev(
                0,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Infra refresh budget set at $40k for the quarter.",
                [
                    _claim(
                        "irb-1",
                        "infra refresh",
                        "budget",
                        "$40,000",
                        0,
                        "Infra refresh budget set at $40k",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                4,
                "s2",
                "slack",
                "DevOps Bot",
                "#infra",
                "Budget corrected to $48k after the capacity estimate landed.",
                [
                    _claim(
                        "irb-2",
                        "infra refresh",
                        "budget",
                        "$48,000",
                        4,
                        "Budget corrected to $48k",
                        "DevOps Bot",
                        "slack",
                        supersedes="irb-1",
                    )
                ],
            ),
            _ev(
                9,
                "s3",
                "slack",
                "DevOps Bot",
                "#infra",
                "Updated infra refresh budget to $55k — networking scope added.",
                [
                    _claim(
                        "irb-3",
                        "infra refresh",
                        "budget",
                        "$55,000",
                        9,
                        "infra refresh budget to $55k",
                        "DevOps Bot",
                        "slack",
                        supersedes="irb-2",
                    )
                ],
            ),
            _ev(
                15,
                "s4",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Final call: infra refresh budget is $60k effective immediately.",
                [
                    _claim(
                        "irb-4",
                        "infra refresh",
                        "budget",
                        "$60,000",
                        15,
                        "infra refresh budget is $60k",
                        "Meeting notes",
                        "meeting",
                        supersedes="irb-3",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "What is the current infra refresh budget?",
                "answer": "$60,000",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["irb-4"],
            },
            {
                "question": "What was the infra refresh budget before the most recent change?",
                "answer": "$55,000",
                "qtype": "temporal",
                "gold_claim_keys": ["irb-3"],
            },
            {
                "question": "What was the very first infra refresh budget?",
                "answer": "$40,000",
                "rubric": ["$40,000"],
                "qtype": "lookup",
                "gold_claim_keys": ["irb-1"],
            },
            {
                "question": "What is the infra refresh uptime target?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def latent_value_conflict() -> dict:
    """Two active values for one predicate with NO typed CONTRADICTS edge."""
    return {
        "scenario_id": "latent_value_conflict",
        "description": "Two sources assert different owners; no contradiction was ever recorded.",
        "entities": [
            {
                "name": "onboarding flow",
                "type": "project",
                "aliases": ["onboarding", "signup flow"],
            },
            {"name": "Dev Bot", "type": "system", "aliases": ["dev bot"]},
        ],
        "events": [
            _ev(
                2,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Onboarding flow is owned by the Platform engineering team.",
                [
                    _claim(
                        "onw-1",
                        "onboarding flow",
                        "owned_by",
                        "Platform engineering",
                        2,
                        "onboarding flow is owned by the Platform engineering team",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                9,
                "s2",
                "slack",
                "Dev Bot",
                "#growth",
                "Onboarding flow ownership moved to the Growth team, per the reorg.",
                [
                    _claim(
                        "onw-2",
                        "onboarding flow",
                        "owned_by",
                        "Growth team",
                        9,
                        "Onboarding flow ownership moved to the Growth team",
                        "Dev Bot",
                        "slack",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "Who owns the onboarding flow?",
                "answer": (
                    "Unresolved conflict: the platform and growth teams both claim the "
                    "onboarding flow. The records were never reconciled."
                ),
                "rubric": ["conflict", "Platform engineering", "Growth team"],
                "qtype": "conflict",
                "gold_claim_keys": ["onw-1", "onw-2"],
            },
            {
                "question": "Who owned the onboarding flow before the reorg?",
                "answer": "Platform engineering",
                "qtype": "temporal",
                "gold_claim_keys": ["onw-1"],
            },
            {
                "question": "What is the onboarding flow's conversion target?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def alias_only_reference() -> dict:
    """The question names the entity by an alias only, never its canonical name."""
    return {
        "scenario_id": "alias_only_reference",
        "description": "The query refers to the 'search' system by a nickname no ticket uses.",
        "entities": [
            {
                "name": "search relevance",
                "type": "system",
                "aliases": ["search", "the ranker", "rank service"],
            },
            {"name": "Ada Osei", "type": "person", "aliases": ["Ada", "@ada"]},
        ],
        "events": [
            _ev(
                0,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Ada Osei will own the search relevance work for the quarter.",
                [
                    _claim(
                        "al-1",
                        "search relevance",
                        "owned_by",
                        "Ada Osei",
                        0,
                        "Ada Osei will own the search relevance work",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                6,
                "s2",
                "slack",
                "Ada Osei",
                "#search",
                "Starting on the ranker today — relevance cutover is next Monday.",
                [
                    _claim(
                        "al-2",
                        "Ada Osei",
                        "works_on",
                        "search relevance",
                        6,
                        "Starting on the ranker",
                        "Ada Osei",
                        "slack",
                        explicitness=0.9,
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "Who owns the search relevance work?",
                "answer": "Ada Osei",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["al-1"],
            },
            {
                "question": "Who is working on the ranker now?",
                "answer": "Ada Osei",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["al-2"],
            },
            {
                "question": "What metrics does the rank service track?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def exact_as_of_read() -> dict:
    """An 'as of' query landing exactly on the day a deadline changed."""
    return {
        "scenario_id": "exact_as_of_read",
        "description": "A delivery commitment shifts; the query asks 'as of' the exact switch day.",
        "entities": [
            {
                "name": "checkout revamp",
                "type": "project",
                "aliases": ["checkout revamp", "the revamp"],
            },
            {"name": "Nadia Hsu", "type": "person", "aliases": ["Nadia", "@nadia"]},
        ],
        "events": [
            _ev(
                0,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Checkout revamp ships on June 15.",
                [
                    _claim(
                        "eo-1",
                        "checkout revamp",
                        "deadline",
                        "2026-06-15",
                        0,
                        "Checkout revamp ships on June 15",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                10,
                "s2",
                "slack",
                "Nadia Hsu",
                "#checkout",
                "Moved the checkout revamp to June 22 — the tax changes need a week more.",
                [
                    _claim(
                        "eo-2",
                        "checkout revamp",
                        "deadline",
                        "2026-06-22",
                        10,
                        "Moved the checkout revamp to June 22",
                        "Nadia Hsu",
                        "slack",
                        supersedes="eo-1",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "What is the current checkout revamp deadline?",
                "answer": "2026-06-22",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["eo-2"],
            },
            {
                "question": "What was the checkout revamp deadline as of the day the tax change was announced?",
                "answer": "2026-06-15",
                "qtype": "temporal",
                "gold_claim_keys": ["eo-1"],
            },
            {
                "question": "What is the checkout revamp's regional launch scope?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def multi_vendor_decision() -> dict:
    """A decision synthesised across sessions with a later contradiction."""
    return {
        "scenario_id": "multi_vendor_decision",
        "description": "A vendor is picked in one meeting, then challenged in Slack late.",
        "entities": [
            {
                "name": "email platform",
                "type": "system",
                "aliases": ["email", "email vendor"],
            },
            {"name": "Tara Ellis", "type": "person", "aliases": ["Tara", "@tara"]},
        ],
        "events": [
            _ev(
                0,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Decided: the email platform will be SendGrid for the next year.",
                [
                    _claim(
                        "mv-1",
                        "email platform",
                        "decided",
                        "SendGrid",
                        0,
                        "the email platform will be SendGrid",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                5,
                "s2",
                "slack",
                "Tara Ellis",
                "#email",
                "Pushed back in the thread: Mailgun is the better deal and we should switch before contracting.",
                [
                    _claim(
                        "mv-2",
                        "email platform",
                        "decided",
                        "Mailgun",
                        5,
                        "Mailgun is the better deal",
                        "Tara Ellis",
                        "slack",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "Which email platform was decided on?",
                "answer": (
                    "Unresolved conflict: the planning meeting picked SendGrid, but Tara "
                    "later argued for Mailgun. Both decisions were recorded and never reconciled."
                ),
                "rubric": ["SendGrid", "Mailgun"],
                "qtype": "conflict",
                "gold_claim_keys": ["mv-1", "mv-2"],
            },
            {
                "question": "Which email platform was initially decided on?",
                "answer": "SendGrid",
                "qtype": "temporal",
                "gold_claim_keys": ["mv-1"],
            },
            {
                "question": "What is the email platform's annual cost?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def reporting_chain() -> dict:
    """A reports_to chain: owner handoff in one branch, manager lookup elsewhere."""
    return {
        "scenario_id": "reporting_chain",
        "description": "Who a person reports to, plus a stale direct-report edge elsewhere.",
        "entities": [
            {"name": "Ivy Norton", "type": "person", "aliases": ["Ivy", "@ivy"]},
            {"name": "Omar Reyes", "type": "person", "aliases": ["Omar", "@omar"]},
            {"name": "Priya Shah", "type": "person", "aliases": ["Priya", "@priya"]},
        ],
        "events": [
            _ev(
                0,
                "s1",
                "linear",
                "HR Bot",
                "hr",
                "HR-11: Ivy Norton reports to Omar Reyes.",
                [
                    _claim(
                        "rp-1",
                        "Ivy Norton",
                        "reports_to",
                        "Omar Reyes",
                        0,
                        "Ivy Norton reports to Omar Reyes",
                        "HR Bot",
                        "linear",
                    )
                ],
            ),
            _ev(
                3,
                "s2",
                "slack",
                "Ivy Norton",
                "#general",
                "Tagging Omar — I'm now reporting to Priya Shah starting today.",
                [
                    _claim(
                        "rp-2",
                        "Ivy Norton",
                        "reports_to",
                        "Priya Shah",
                        3,
                        "I'm now reporting to Priya Shah",
                        "Ivy Norton",
                        "slack",
                        supersedes="rp-1",
                    )
                ],
            ),
            _ev(
                4,
                "s3",
                "linear",
                "HR Bot",
                "hr",
                "HR-12: Omar Reyes still listed as Ivy Norton's manager (stale).",
                [
                    _claim(
                        "rp-3",
                        "Ivy Norton",
                        "reports_to",
                        "Omar Reyes",
                        4,
                        "Omar Reyes still listed as Ivy Norton's manager",
                        "HR Bot",
                        "linear",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "Who does Ivy Norton report to?",
                "answer": "Priya Shah",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["rp-2"],
            },
            {
                "question": "Who did Ivy Norton report to before the change?",
                "answer": "Omar Reyes",
                "qtype": "temporal",
                "gold_claim_keys": ["rp-1"],
            },
            {
                "question": "What is Ivy Norton's direct deposit account?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def partial_status_chain() -> dict:
    """Mixed chain where an intermediate supersession is missing (gap tests depth)."""
    return {
        "scenario_id": "partial_status_chain",
        "description": "A blocker resolves without a recorded intermediate step; only head + tail.",
        "entities": [
            {
                "name": "billing service",
                "type": "system",
                "aliases": ["billing", "billing svc"],
            },
            {"name": "Ravi Nair", "type": "person", "aliases": ["Ravi", "@ravi"]},
        ],
        "events": [
            _ev(
                0,
                "s1",
                "slack",
                "Ravi Nair",
                "#eng",
                "Billing service is blocked by the payments outage.",
                [
                    _claim(
                        "bs-1",
                        "billing service",
                        "status",
                        "blocked by payments outage",
                        0,
                        "billing service is blocked by the payments outage",
                        "Ravi Nair",
                        "slack",
                    )
                ],
            ),
            _ev(
                3,
                "s2",
                "slack",
                "Ravi Nair",
                "#eng",
                "Payments is back; billing service has resumed normal operation.",
                [
                    _claim(
                        "bs-2",
                        "billing service",
                        "status",
                        "operational",
                        3,
                        "billing service resumed normal operation",
                        "Ravi Nair",
                        "slack",
                        supersedes="bs-1",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "What is the current status of the billing service?",
                "answer": "operational",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["bs-2"],
            },
            {
                "question": "What was blocking the billing service?",
                "answer": "payments outage",
                "qtype": "temporal",
                "gold_claim_keys": ["bs-1"],
            },
            {
                "question": "What is the billing service's error budget?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
        ],
    }


def prefers_shift() -> dict:
    """A preference predicate with a clean supersede — tests non-temporally-ringed facts."""
    return {
        "scenario_id": "prefers_shift",
        "description": "An engineer's preferred language shifts; org note lags the decision.",
        "entities": [
            {"name": "Samir Ali", "type": "person", "aliases": ["Samir", "@samir"]},
        ],
        "events": [
            _ev(
                0,
                "s1",
                "meeting",
                "Meeting notes",
                "fireflies",
                "Samir Ali prefers TypeScript for greenfield services.",
                [
                    _claim(
                        "pf-1",
                        "Samir Ali",
                        "prefers",
                        "TypeScript",
                        0,
                        "Samir Ali prefers TypeScript",
                        "Meeting notes",
                        "meeting",
                    )
                ],
            ),
            _ev(
                8,
                "s2",
                "slack",
                "Samir Ali",
                "#general",
                "I've moved to Go for new services, overriding my earlier preference.",
                [
                    _claim(
                        "pf-2",
                        "Samir Ali",
                        "prefers",
                        "Go",
                        8,
                        "I've moved to Go for new services",
                        "Samir Ali",
                        "slack",
                        supersedes="pf-1",
                    )
                ],
            ),
        ],
        "qa": [
            {
                "question": "What language does Samir Ali prefer now?",
                "answer": "Go",
                "qtype": "knowledge_update",
                "gold_claim_keys": ["pf-2"],
            },
            {
                "question": "What language did Samir Ali prefer originally?",
                "answer": "TypeScript",
                "qtype": "temporal",
                "gold_claim_keys": ["pf-1"],
            },
            {
                "question": "What is Samir Ali's GitHub handle?",
                "answer": "ABSTAIN",
                "qtype": "abstention",
                "gold_claim_keys": [],
            },
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
    deep_supersession_chain,
    latent_value_conflict,
    alias_only_reference,
    exact_as_of_read,
    multi_vendor_decision,
    reporting_chain,
    partial_status_chain,
    prefers_shift,
]
