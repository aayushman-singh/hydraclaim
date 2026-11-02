# Qualify independent decisions and preferences

## Status

Accepted.

## Problem

An owner can make several independent choices. A later choice must not close an earlier choice about another matter. A read must return the choice that the question names.

## Decision

A Claim with predicate `decided` or `prefers` has a required canonical `qualifier`. The Subject continues to identify the actor. The qualifier identifies the independent choice. Other predicates have no qualifier.

Ordinary extraction copies a qualifier phrase from the exact supporting Evidence. It uses separate passages for different choices in one message. Ambiguous evidence stops the extraction before a Claim write.

Duplicate detection, Supersession, and Conflict use the complete subject, predicate, and qualifier. A different qualified value replaces an active Claim only when the extraction names that Claim as an explicit supersession target. A missing target or a different scope stops the operation. Without an explicit correction, different values in the same scope remain a Conflict.

Read selection has three distinct states. A string selects one exact qualifier. `QualifierSelection.ALL` requests an intentional bounded listing. `None` selects an unqualified predicate. A missing or unknown specific qualifier does not select all choices. A read validates the bounded candidate set before it filters exact scope. Missing required legacy qualifiers cause an integrity error.

Generic listings retain each qualifier. Temporal comparisons require one qualifier. A Supersession chain cannot cross qualifiers. An absent relation invalidation marker keeps the old relation valid. A nonempty `invalidated_by` marker excludes the relation from valid traversal. A malformed marker causes an integrity error.

## Existing records

A bounded writer-owned repair preserves Claim keys, Evidence quotes, Sources, and Source Events. It verifies the complete exact input set before a mutation. A reviewed legacy semantic mapping can qualify old records whose original quote does not contain a common literal scope. This exceptional data migration does not change ordinary extraction validation.

A false historical relation stays stored with an explicit repair marker. Only a proved false closure is restored. Private before and after receipts retain the reviewed mappings and any partial effects. The operator pauses the ordinary writer during apply.

## Consequences

A Claim with an unrelated qualifier stays independent. A read can abstain or fail explicitly when it cannot identify valid scope. No old-schema mode, inferred scope, hidden retry, or substituted answer is available.
