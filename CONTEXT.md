# HydraClaim

HydraClaim stores time-based claims for AI agent memory. It gives answers only when stored evidence supports them.

## Language

**Claim**:
A fact statement about one subject with one predicate, one value, and one validity period.
_Avoid_: Fact, memory item

**Subject**:
The entity that a claim describes.
_Avoid_: Topic, target

**Predicate**:
A controlled relation that states what a claim says about its subject.
_Avoid_: Field, property

**Evidence**:
The exact source text that supports a claim.
_Avoid_: Context, excerpt

**Source**:
The author and source kind from which evidence comes.
_Avoid_: Origin, provider

**Provenance**:
The relation from a claim through its evidence to its source.
_Avoid_: Metadata, lineage

**Supersession**:
A directed relation from a new claim to the older claim that it replaces.
_Avoid_: Update, overwrite

**Conflict**:
An unresolved relation between active claims that give different values for the same subject and predicate.
_Avoid_: Mismatch, disagreement

**Abstention**:
An answer result that states that stored claims do not support an answer.
_Avoid_: Failure, empty answer

**Probe**:
A bounded read of claim coverage, conflicts, active values, and supersession depth for one subject and optional predicate.
_Avoid_: Search, scan

**Route**:
The selected answer path: `FAST`, `DEEP`, or `ABSTAIN`.
_Avoid_: Mode, strategy

## Relationships

- A **Claim** describes exactly one **Subject** through exactly one **Predicate**.
- A **Claim** has one **Evidence** record.
- **Evidence** comes from one **Source**.
- A **Claim** can supersede zero or more older **Claims**.
- A **Claim** can conflict with zero or more active **Claims**.
- A **Probe** measures claims for one **Subject** and an optional **Predicate**.
- A **Probe** and question type select one **Route**.
- The `ABSTAIN` **Route** produces an **Abstention**.

## Example dialogue

> **Developer:** "Two active claims give different owners for the same subject. Is this supersession?"
> **Domain expert:** "No. It is a conflict until evidence states that one claim replaces the other."

## Flagged ambiguities

- "Fact" can mean a **Claim** or a verified conclusion. Use **Claim** for all stored statements.
- "History" can mean all claims or one supersession chain. State which meaning applies.
