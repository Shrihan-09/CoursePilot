"""Curated query expansion (Phase 5.0, Part E).

## Why this exists instead of embeddings

Measured on the real corpus, BM25F reached R@5 = 0.725, R@10 = 0.792,
MRR = 0.775. It failed completely on exactly one addressable query type:

```
synonym    "AI"  -> 0.000        "ML"  -> 0.000
```

The reason is not subtle. No Rutgers field anywhere contains the token `AI`
or `ML`; the titles say ARTIFICIAL INTELLIGENCE and MACHINE LEARNING. No
amount of lexical scoring can bridge that, because the string is simply
absent.

The other failing type, `requirement_oriented`, is **not a retrieval problem
at all** - which courses satisfy CS_ELECTIVES is decided by the Degree
Engine, applying the 300-level rule, the outside-subject cap and the
exclusion rules. Retrieval must not try to answer it.

So the entire addressable gap was abbreviations. Neural embeddings were
considered and rejected for this corpus: 98% of documents are a ~27-character
uppercase title, which is very little text for a sentence encoder to work
with, and the dependency (torch, ~2GB) is large for closing a gap this
narrow. That decision is recorded in DATA_MODEL.md section 22 with the
numbers behind it.

## This is curated data, not invented semantics

Every entry is an abbreviation in standard use in the domain, expanded to
wording that actually appears in Rutgers course titles. The rules:

  * an expansion must be a phrase the corpus really uses - it is a pointer
    to existing text, never a claim about what a course is about;
  * expansion ADDS terms, it never replaces them, so an exact lookup can
    still win on its own tokens;
  * every applied expansion is reported, so a future answer can say why a
    course surfaced.

That makes this the same kind of artifact as the curated requirement data:
reviewed, attributable, and testable - not a model's opinion.

## What it deliberately does NOT do

It does not stem, it does not guess at topical similarity, and it does not
expand a term into courses. `"AI"` becomes `"AI artificial intelligence"`;
it never becomes a list of course codes. Mapping a query directly to courses
would be retrieval making an academic judgement, which is the boundary this
phase exists to protect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.search.contract import CourseSearcher, SearchResult


@dataclass(frozen=True, slots=True)
class Expansion:
    """One curated abbreviation, with the reason it is defensible."""

    term: str
    expands_to: tuple[str, ...]
    note: str


#: Reviewed abbreviation map. Each entry names wording that appears verbatim
#: in Rutgers course titles or subject names, so the expansion points at real
#: text rather than asserting a topic.
CURATED_EXPANSIONS: tuple[Expansion, ...] = (
    Expansion(
        "ai",
        ("artificial", "intelligence"),
        "Titles say ARTIFICIAL INTELLIGENCE; no title contains the token 'AI'.",
    ),
    Expansion(
        "ml",
        ("machine", "learning"),
        "Titles say MACHINE LEARNING; no title contains the token 'ML'.",
    ),
    Expansion(
        "os",
        ("operating", "systems"),
        "01:198:416 is OPERATING SYSTEMS DESIGN.",
    ),
    Expansion(
        "cs",
        ("computer", "science"),
        "subject.description for subject 198 is 'Computer Science'.",
    ),
    Expansion(
        "db",
        ("database",),
        "Standard abbreviation; catalog descriptions use 'database'.",
    ),
    Expansion(
        "stats",
        ("statistics",),
        "subject.description for 960 is 'Statistics'.",
    ),
    Expansion(
        "orgo",
        ("organic", "chemistry"),
        "Widely used student abbreviation; titles say ORGANIC CHEMISTRY.",
    ),
    Expansion(
        "diffeq",
        ("differential", "equations"),
        "Student abbreviation; titles say DIFFERENTIAL EQUATIONS.",
    ),
)

_BY_TERM = {e.term: e for e in CURATED_EXPANSIONS}
_TOKEN = re.compile(r"[a-z0-9]+")


def expand_query(query: str) -> tuple[str, tuple[Expansion, ...]]:
    """Add curated expansions to a query, keeping the original tokens.

    Returns the expanded query and which expansions fired, so the reason a
    course surfaced is reportable rather than mysterious.
    """
    tokens = _TOKEN.findall(query.lower())
    applied: list[Expansion] = []
    extra: list[str] = []
    for token in tokens:
        expansion = _BY_TERM.get(token)
        if expansion is None:
            continue
        applied.append(expansion)
        extra.extend(expansion.expands_to)

    if not extra:
        return query, ()
    # Original first: an exact lookup must still be able to win on its own
    # tokens rather than being outvoted by the expansion.
    return f"{query} {' '.join(extra)}", tuple(applied)


@dataclass(slots=True)
class ExpandingSearcher:
    """Wraps any `CourseSearcher` with curated query expansion.

    A decorator rather than a change to BM25, so the two can be measured
    separately - which is the only way to say what the expansion is worth.
    """

    inner: CourseSearcher
    name: str = field(default="")
    last_expansions: tuple[Expansion, ...] = field(default=(), init=False)

    def __post_init__(self) -> None:
        if not self.name:
            self.name = f"{getattr(self.inner, 'name', 'searcher')}+expansion"

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        expanded, applied = expand_query(query)
        self.last_expansions = applied
        return self.inner.search(expanded, limit=limit)


__all__ = [
    "CURATED_EXPANSIONS",
    "Expansion",
    "ExpandingSearcher",
    "expand_query",
]
