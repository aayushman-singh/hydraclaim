# Hack Hydra submission packet

## Project name

HydraClaim

## Selected track

Track 3: Memory and context retrieval

## Short project description

HydraClaim gives AI agents conflict-aware, time-based memory. It stores each
statement as a claim with exact evidence, source provenance, and a validity
period. HydraDB relations record supersession and unresolved conflict. A graph
probe measures claim coverage and conflict before each answer. HydraClaim uses
that graph state to select a fast read, a deep conflict read, or an explicit
abstention. It also saves source events before extraction, records failed
processing attempts, and lets users inspect the complete claim history.

## Problem

Agent memory retrieves related text but often does not know which statement is
current, which source conflicts with another source, or whether stored evidence
supports an answer. This can produce stale answers and confident guesses.

HydraClaim makes these conditions explicit. It answers two important questions:

1. What is supported now or at a selected time?
2. When must the agent not answer?

## What I built

- A time-based claim graph with evidence and source provenance.
- Directed `SUPERSEDES` relations for changed statements.
- Directed `CONTRADICTS` relations for unresolved active conflicts.
- Exact coverage checks that produce explicit abstention.
- Fast and deep answer routes selected from measured graph state.
- Predicate-specific trust scoring for true conflicts.
- Durable source-event capture before language-model extraction.
- Extraction attempt and failure records.
- A command-line interface, HTTP application programming interface (API), and
  public web demo.
- A deterministic benchmark with 50 questions across 16 conflict and time
  scenarios.

## How HydraDB is used

HydraDB is the system of record. It stores entities, claims, evidence, sources,
source events, extraction attempts, and failure records.

HydraClaim uses typed HydraDB relations to store information that flat memory
must infer during every read:

- `ABOUT` connects a claim to its subject.
- `SUPPORTED_BY` connects a claim to exact evidence.
- `SUPERSEDES` stores replacement history.
- `CONTRADICTS` stores unresolved conflict.
- `PRODUCED_BY` attributes a claim to one extraction attempt.
- `QUOTED_FROM` connects evidence to the accepted source event.

Bounded graph reads measure coverage, conflict count, and supersession depth.
Property filters implement time-based reads. Without HydraDB, HydraClaim would
have to reconstruct these relations from similar text during every question.

## Quality of results

The reproducible 50-question suite reports:

- Router plus graph probe accuracy: 0.980.
- Abstention precision: 0.941.
- Abstention recall: 1.000.
- Naive retrieval-augmented generation baseline accuracy: 0.280.

The suite includes changed knowledge, source conflicts, time boundaries, entity
aliases, and questions with no supporting claim.

## Technology stack

- Python 3.11 through 3.13.
- HydraDB and its verified OpenCypher subset.
- Standard-library command-line and HTTP interfaces.
- `httpx` for the HydraDB HTTP connection.
- A static HTML, Cascading Style Sheets (CSS), and JavaScript web application.
- Vercel for the web application.
- A Contabo service for the public API and HydraDB node.
- GitHub Actions and trusted Python Package Index (PyPI) publishing.

## Team and contribution

Aayushman Singh. Solo builder. I designed and implemented the graph model,
ingestion, extraction, reconciliation, routing, retrieval, benchmark, command
line, API, web demo, deployment, tests, and documentation.

## Links

- Repository: https://github.com/aayushman-singh/hydraclaim
- Live application: https://hydraclaim.aayushman.dev
- Public API: https://hydraclaim-api.aayushman.dev
- Demo video: https://youtu.be/qa5agsQvzfA
- Release: https://github.com/aayushman-singh/hydraclaim/releases/tag/v0.2.0

## Video checklist

The public video is 2 minutes 13 seconds. It covers the problem, working demo,
graph model, benchmark, and the reason HydraDB is necessary.

## Final submission checklist

1. Open each link in a private browser window.
2. Paste the sections above into the official form.
3. Select Track 3: Memory and context retrieval.
4. Confirm the solo team entry.
5. Submit the form before August 20, 2026 at 11:59 PM Pacific Time.
6. Save the submission confirmation.
