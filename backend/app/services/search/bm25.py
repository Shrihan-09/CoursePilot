"""BM25 lexical retrieval (Phase 5.0, Part D).

Implemented directly rather than pulled in as a dependency. BM25 is about
sixty lines of arithmetic, and writing it means the ranking is inspectable
and the tokenizer can handle Rutgers course codes, which a generic library
would split into meaningless fragments.

## The ranking function

For a query term `t` and document `d`:

    idf(t)  = ln( 1 + (N - df + 0.5) / (df + 0.5) )

    score   = sum over t of  idf(t) * ( f * (k1 + 1) )
                             / ( f + k1 * (1 - b + b * len/avglen) )

`f` is the term frequency. This is **BM25F**, and the important detail is
that each field is length-normalised **against its own average**:

    f = sum over fields of   w_field * tf_field
                             / (1 - b + b * len_field / avglen_field)

An earlier version summed the field lengths into one document length and
normalised once. Measured on the real corpus that was badly wrong: only 31
of 4,415 documents carry a catalog description, so the corpus mean length
was 13.4 tokens while a described document ran to 43. Every described course
was then penalised for carrying more information - 01:198:112 did not appear
in the top ten for its own exact title, "data structures", while three
graduate courses did.

Per-field normalisation fixes that at the root: a description is compared
with other descriptions, and a title with other titles.

`k1` damps repetition - the tenth occurrence of a word says little more than
the third. `b` controls how hard length is normalised. Both are the standard
defaults and are stated here rather than buried.

## Field weights are NOT invented

Every weight defaults to 1.0. Phase 5.0's brief is explicit that weights must
not be made up without evaluation, so tuning happens in the evaluation
harness against the labelled query set, and any non-default weight shipped
later has to point at a measurement.

## Course codes

Rutgers writes a course as `01:198:344`. Students write `CS 344`, `198:344`,
or `cs344`. The tokenizer emits the full string, each colon-separated part,
and the joined subject+number, so all of those forms reach the same document
without a special-case branch per query shape.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.services.search.contract import SearchResult, result_from_document
from app.services.search.documents import CourseDocument

#: Standard BM25 parameters. Stated, not hidden.
K1 = 1.5
B = 0.75

#: All 1.0 until evaluation says otherwise. See the module docstring.
DEFAULT_FIELD_WEIGHTS: dict[str, float] = {
    "code": 1.0,
    "subject": 1.0,
    "number": 1.0,
    "title": 1.0,
    "description": 1.0,
}

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens. Deliberately simple and deterministic.

    No stemming: "algorithm" and "algorithms" stay distinct. Stemming is a
    tuning decision that should be justified by the evaluation set rather
    than assumed, and it would quietly change what an exact-lookup query
    matches.
    """
    return _TOKEN.findall(text.lower())


def tokenize_course_key(course_key: str) -> list[str]:
    """Emit every form a student might type for one course code.

    `01:198:344` -> ['01', '198', '344', '01198344', '198344', '01:198:344']

    The joined forms exist so `cs344`-style queries, once the subject is
    resolved to its number, still land on the right document.
    """
    parts = [p for p in course_key.split(":") if p]
    tokens = list(parts)
    if len(parts) >= 2:
        tokens.append("".join(parts))
        tokens.append("".join(parts[-2:]))
    tokens.append(course_key.lower().replace(":", ""))
    return [t.lower() for t in tokens]


@dataclass(slots=True)
class BM25Index:
    """An in-memory BM25F index over course documents.

    In memory because the corpus is 4,415 short documents - a few megabytes.
    A PostgreSQL full-text index would be the answer at a scale this corpus
    is nowhere near, and adding one now would be infrastructure without a
    measurement behind it.
    """

    documents: list[CourseDocument] = field(default_factory=list)
    field_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FIELD_WEIGHTS))
    #: term -> {document index -> normalised weighted frequency}
    postings: dict[str, dict[int, float]] = field(default_factory=dict)
    #: term -> {document index -> set of fields it appeared in}
    term_fields: dict[str, dict[int, set[str]]] = field(default_factory=dict)
    #: field -> per-document token count, and that field's corpus average.
    field_length: dict[str, list[int]] = field(default_factory=dict)
    average_field_length: dict[str, float] = field(default_factory=dict)
    doc_length: list[float] = field(default_factory=list)
    average_length: float = 0.0

    def build(self, documents: list[CourseDocument]) -> BM25Index:
        self.documents = documents
        self.postings = defaultdict(dict)
        self.term_fields = defaultdict(dict)
        self.doc_length = []

        # Pass 1: tokenize once, and measure each field against its OWN average.
        tokenized: list[dict[str, list[str]]] = []
        self.field_length = {name: [] for name in DEFAULT_FIELD_WEIGHTS}

        for document in documents:
            per_field: dict[str, list[str]] = {}
            for field_name, text in document.field_text().items():
                tokens = (
                    tokenize_course_key(text)
                    if field_name == "code"
                    else tokenize(text)
                )
                per_field[field_name] = tokens
                self.field_length.setdefault(field_name, []).append(len(tokens))
            tokenized.append(per_field)

        self.average_field_length = {}
        for name, lengths in self.field_length.items():
            # Averaged over documents that HAVE the field. Including the 4,384
            # courses with no description would drag the description average
            # toward zero and make any described document look enormous.
            present = [n for n in lengths if n > 0]
            self.average_field_length[name] = (
                sum(present) / len(present) if present else 1.0
            )

        # Pass 2: accumulate per-field-normalised weighted frequencies.
        for index, per_field in enumerate(tokenized):
            for field_name, tokens in per_field.items():
                if not tokens:
                    continue
                weight = self.field_weights.get(field_name, 1.0)
                avg = self.average_field_length.get(field_name) or 1.0
                norm = 1 - B + B * (len(tokens) / avg)
                counts: dict[str, int] = {}
                for token in tokens:
                    counts[token] = counts.get(token, 0) + 1
                for token, count in counts.items():
                    postings = self.postings[token]
                    postings[index] = postings.get(index, 0.0) + weight * count / norm
                    self.term_fields[token].setdefault(index, set()).add(field_name)
            self.doc_length.append(float(sum(len(t) for t in per_field.values())))

        self.average_length = (
            sum(self.doc_length) / len(documents) if documents else 0.0
        )
        return self

    def _idf(self, term: str) -> float:
        n = len(self.documents)
        df = len(self.postings.get(term, ()))
        if df == 0:
            return 0.0
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def score(self, query: str) -> dict[int, tuple[float, set[str]]]:
        """Raw BM25F scores by document index, with the fields that matched."""
        scores: dict[int, float] = defaultdict(float)
        matched: dict[int, set[str]] = defaultdict(set)

        for term in tokenize(query):
            postings = self.postings.get(term)
            if not postings:
                continue
            idf = self._idf(term)
            for doc_index, frequency in postings.items():
                # Length normalisation already happened per field at build
                # time, so the saturation term uses the frequency directly.
                # Normalising twice would re-introduce the bias against
                # documents that carry a description.
                scores[doc_index] += idf * (frequency * (K1 + 1)) / (frequency + K1)
                matched[doc_index] |= self.term_fields[term][doc_index]

        return {i: (s, matched[i]) for i, s in scores.items()}


class BM25Searcher:
    """The BM25 implementation of the `CourseSearcher` contract."""

    name = "bm25"

    def __init__(self, index: BM25Index) -> None:
        self.index = index

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        scored = self.index.score(query)
        # Ties break on the document's natural key, so an identical corpus
        # always produces an identical ranking.
        ordered = sorted(
            scored.items(),
            key=lambda item: (-item[1][0], self.index.documents[item[0]].document_id),
        )
        results = []
        for doc_index, (score, fields) in ordered[:limit]:
            document = self.index.documents[doc_index]
            results.append(
                result_from_document(
                    document,
                    score=score,
                    matched_fields=tuple(sorted(fields)),
                    retriever=self.name,
                )
            )
        return results


def build_bm25(
    documents: list[CourseDocument], field_weights: dict[str, float] | None = None
) -> BM25Searcher:
    index = BM25Index(
        field_weights=dict(field_weights or DEFAULT_FIELD_WEIGHTS)
    ).build(documents)
    return BM25Searcher(index)


__all__ = [
    "B",
    "DEFAULT_FIELD_WEIGHTS",
    "K1",
    "BM25Index",
    "BM25Searcher",
    "build_bm25",
    "tokenize",
    "tokenize_course_key",
]
