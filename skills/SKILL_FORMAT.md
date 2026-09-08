# Skill format

A **skill** is a versioned, on-disk bundle of structured knowledge that the
router can selectively load into an agent request.

Nothing here is implemented yet. This file defines the contract so that skills
authored now remain loadable later.

## The load-bearing rule

> A skill describes **structure and strategy**. It is not the source of truth
> for Rutgers facts.

A skill may say:

- "This program organizes requirements into core, breadth, and capstone groups."
- "When a student asks about graduating early, check summer offerings too."

A skill may **not** be the only place that records:

- which specific course satisfies which specific requirement
- what a course's prerequisites are
- how many credits a program requires

Those are Rutgers facts. They live in the database with provenance, are
retrieved at request time, and are checked by the deterministic validator.

The reason is drift. Skill files are edited by hand and are not term-scoped;
Rutgers requirements change by catalog year. A hand-edited Markdown file
asserting "CS majors need 11 courses" will silently become wrong, and nothing
will catch it. A database row carries its source URL, its catalog year, and a
verification status, so staleness is detectable.

## Layout

```
skills/
├── router/     # intent classification; decides which skills to load
├── shared/     # cross-program knowledge (e.g. university-wide core curriculum)
├── majors/     # one directory per program, e.g. majors/computer-science/
└── tools/      # tool descriptions the agent may call
```

## Manifest

Each skill directory contains `skill.yaml`:

```yaml
skill_id: majors/computer-science
kind: major              # router | shared | major | tool
version: "0.1.0"
description: >
  Requirement structure and planning heuristics for the Computer Science
  program. Structure only; specific requirements come from the database.
depends_on:
  - shared/core-curriculum
approx_tokens: 1200      # so the router can budget context
authoritative_sources:   # what a reader should verify this against
  - TODO(rutgers-source)
```

`SKILL.md` alongside it holds the body.

## Why skills are separate from the database

Two different kinds of knowledge, with different change rates and different
failure modes:

| | Database | Skill |
|---|---|---|
| Content | Rutgers facts | How to reason about a program |
| Changes | Every term, via ingestion | Rarely, by hand |
| Wrong data means | Detectable staleness | Bad advice, silent |
| Consumed by | Validator + retrieval | Agent prompt context |

Putting requirement facts in skills collapses that distinction and puts
unverifiable claims into the model's context — precisely the failure the
architecture exists to prevent.

## Routing

The router loads a skill only when the request needs it. Loading every major's
knowledge into every request wastes context, raises cost, and degrades
retrieval quality by diluting relevant material.

See `docs/AGENT_ARCHITECTURE.md`.
