// HydraClaim graph model on HydraDB (documentation + canonical queries).
// Feature support is verified against a live node by:
//     python -m hydraclaim.schema --verify
//
// ---------------------------------------------------------------------------
// Node shapes
// ---------------------------------------------------------------------------
// (:Entity   {id, name, type, aliases})                      // people, projects, systems
// (:Claim    {id, predicate, value,
//             valid_from, valid_to,                          // event time (bitemporal)
//             recorded_at,                                   // ingestion time
//             status,                                        // active | superseded | disputed
//             confidence})
// (:Evidence {id, quote, ts, session_id, msg_id,
//             extraction_confidence, explicitness})
// (:Source   {id, kind, author, channel})                    // kind: slack | linear | meeting
//
// Edges
// (Claim)-[:ABOUT]->(Entity)
// (Claim)-[:SUPPORTED_BY]->(Evidence)
// (Evidence)-[:FROM]->(Source)
// (Claim)-[:SUPERSEDES {at}]->(Claim)                        // explicit overwrite, new -> old
// (Claim)-[:CONTRADICTS {resolved, detected_at}]->(Claim)    // unresolved conflict
//
// Invariant: claims are never overwritten. A correction creates a new Claim
// plus a SUPERSEDES edge; the old claim keeps its validity window closed by
// valid_to. Contradictions without a supersession edge stay CONTRADICTS
// {resolved: false} until reconciled.

// ---------------------------------------------------------------------------
// Canonical queries (router probe + retrieval paths build on these)
// ---------------------------------------------------------------------------

// 1. Current truth: active claims for one (subject, predicate)
MATCH (c:Claim)-[:ABOUT]->(e:Entity {name: 'payments integration'})
WHERE c.predicate = 'owned_by' AND c.status = 'active'
RETURN c.value, c.valid_from
ORDER BY c.valid_from DESC;

// 2. Time travel: what was believed as of T.
//    An empty valid_to value marks an open validity window.
MATCH (c:Claim)-[:ABOUT]->(e:Entity {name: 'product launch'})
WHERE c.predicate = 'deadline'
  AND c.recorded_at <= '2026-05-12T00:00:00+00:00'
  AND (c.valid_to = '' OR c.valid_to > '2026-05-12')
RETURN c.value, c.valid_from, c.valid_to;

// 3. Supersession chain: chronology of an overwritten claim.
//    HydraDB returns the matching pairs. Compute chain depth client-side
//    from the directed pairs because the dialect has no path-length function.
MATCH p = (newer:Claim)-[:SUPERSEDES*1..5]->(older:Claim)
WHERE newer.predicate = 'deadline'
RETURN newer.id AS newer_id, older.id AS older_id,
       newer.value AS newer_value, older.value AS older_value;

// 4. Unresolved conflicts (deep-path trigger).
//    CONTRADICTS is directed from the first claim to the second claim.
MATCH (a:Claim)-[r:CONTRADICTS]->(b:Claim)
WHERE r.resolved = false
RETURN a.predicate, a.value, b.value, a.valid_from, b.valid_from;

// 5. Coverage probe (abstention trigger): zero rows -> abstain
MATCH (c:Claim)-[:ABOUT]->(e:Entity {name: 'product launch'})
WHERE c.predicate = 'budget'
RETURN count(c) AS coverage;

// 6. Evidence with provenance for citations
MATCH (c:Claim {key: 'deadline_drift:dl-3'})-[:SUPPORTED_BY]->(ev:Evidence)-[:FROM]->(s:Source)
RETURN ev.quote, ev.ts, s.kind, s.author;
