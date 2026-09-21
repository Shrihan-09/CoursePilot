"""Course retrieval: documents, BM25, provenance, evaluation (Phase 5.0).

Retrieval is measured, not eyeballed. These tests pin the contract and the
tokenizer; the metric numbers themselves live in the evaluation harness and
are reported in DATA_MODEL.md, because they move with the corpus.

## The boundary these tests defend

    RAG retrieves and explains. The deterministic Degree Engine decides.

`test_search_never_claims_requirement_satisfaction` is the guard: nothing in
`app.services.search` may import the audit engine or answer an eligibility
question.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from app.services.search.bm25 import (
    DEFAULT_FIELD_WEIGHTS,
    BM25Index,
    build_bm25,
    tokenize,
    tokenize_course_key,
)
from app.services.search.contract import SearchResult
from app.services.search.documents import (
    CourseDocument,
    Provenance,
    build_course_documents,
    corpus_stats,
)
from app.services.search.evaluation import EvalQuery, QueryOutcome, evaluate

EVAL_PATH = pathlib.Path(__file__).parent / "data" / "retrieval_eval.json"


def _doc(key, title, *, subject="Computer Science", description=None, number=None):
    return CourseDocument(
        document_id=f"{key}|",
        course_key=key,
        subject_code=key.split(":")[1],
        subject_name=subject,
        course_number=number or key.split(":")[2],
        title=title,
        description=description,
        credits=None,
        level="U",
        course_provenance=Provenance(source_kind="rutgers_soc"),
        description_provenance=(
            Provenance(source_kind="rutgers_catalog", catalog_year="2026-2027")
            if description
            else None
        ),
    )


_CORPUS = [
    _doc("01:198:111", "INTRO COMPUTER SCI", description="Problem solving through decomposition."),
    _doc("01:198:112", "DATA STRUCTURES", description="Queues, stacks, trees, lists, and recursion."),
    _doc("01:198:344", "DESIGN AND ANALYSIS OF COMPUTER ALGORITHMS"),
    _doc("01:198:416", "OPERATING SYSTEMS DESIGN"),
    _doc("01:198:440", "INTRODUCTION TO ARTIFICIAL INTELLIGENCE"),
    _doc("01:198:461", "MACHINE LEARNING PRINCIPLES"),
    _doc("01:640:104", "INTRODUCTION TO PROBABILITY", subject="Mathematics"),
    _doc("16:198:512", "INTRODUCTION TO DATA STRUCTURES AND ALGORITHMS"),
]


@pytest.fixture
def searcher():
    return build_bm25(_CORPUS)


# ==========================================================================
# tokenizer
# ==========================================================================


def test_tokenize_is_lowercase_alphanumeric() -> None:
    assert tokenize("Design AND Analysis, of  Algorithms!") == [
        "design",
        "and",
        "analysis",
        "of",
        "algorithms",
    ]


def test_course_key_tokenizer_emits_every_written_form() -> None:
    """Students write the same course several ways; all must reach it."""
    tokens = set(tokenize_course_key("01:198:344"))
    assert {"01", "198", "344"} <= tokens
    assert "01198344" in tokens      # fully joined
    assert "198344" in tokens        # subject+number


def test_tokenize_does_not_stem() -> None:
    """Deliberate: stemming would change what an exact lookup matches, and
    that is a tuning decision the evaluation set has to justify."""
    assert tokenize("algorithms") == ["algorithms"]
    assert tokenize("algorithm") == ["algorithm"]


# ==========================================================================
# BM25 retrieval
# ==========================================================================


def test_exact_course_code_ranks_its_own_course_first(searcher) -> None:
    results = searcher.search("01:198:344", limit=5)
    assert results[0].course_key == "01:198:344"
    assert "code" in results[0].matched_fields


def test_subject_name_plus_number_finds_the_course(searcher) -> None:
    results = searcher.search("computer science 344", limit=5)
    assert results[0].course_key == "01:198:344"


def test_exact_title_ranks_first_even_with_a_description(searcher) -> None:
    """The regression that per-field normalisation fixed.

    01:198:112 is titled exactly "DATA STRUCTURES" AND carries a description.
    Normalising the whole document by one corpus-average length penalised it
    for the extra text, so a longer graduate title outranked it. Each field
    is now normalised against its own average.
    """
    results = searcher.search("data structures", limit=5)
    assert results[0].course_key == "01:198:112"


def test_title_tokens_retrieve_the_course(searcher) -> None:
    results = searcher.search("machine learning", limit=5)
    assert results[0].course_key == "01:198:461"


def test_description_terms_are_searchable(searcher) -> None:
    """'recursion' appears in no title anywhere in the real corpus."""
    results = searcher.search("recursion", limit=5)
    assert results[0].course_key == "01:198:112"
    assert "description" in results[0].matched_fields


def test_search_is_case_insensitive(searcher) -> None:
    lower = [r.course_key for r in searcher.search("operating systems", limit=3)]
    upper = [r.course_key for r in searcher.search("OPERATING SYSTEMS", limit=3)]
    assert lower == upper


def test_no_results_for_terms_absent_from_the_corpus(searcher) -> None:
    """An empty list, not a low-confidence guess."""
    assert searcher.search("underwater basket weaving", limit=5) == []


def test_ranking_is_deterministic(searcher) -> None:
    runs = [
        [(r.course_key, round(r.score, 9)) for r in searcher.search("algorithms", limit=5)]
        for _ in range(5)
    ]
    assert all(run == runs[0] for run in runs)


def test_ties_break_on_the_natural_key() -> None:
    """Two identical titles must order by course key, not by insertion or by
    a surrogate id - the index has to be reproducible across a re-ingest."""
    corpus = [_doc("01:198:999", "SAME TITLE"), _doc("01:198:888", "SAME TITLE")]
    results = build_bm25(corpus).search("same title", limit=2)
    assert [r.course_key for r in results] == ["01:198:888", "01:198:999"]


def test_limit_is_respected(searcher) -> None:
    assert len(searcher.search("computer", limit=2)) <= 2


def test_matched_fields_explain_the_hit(searcher) -> None:
    """A code hit and a description hit are different kinds of answer."""
    code_hit = searcher.search("01:198:416", limit=1)[0]
    assert "code" in code_hit.matched_fields
    text_hit = searcher.search("recursion", limit=1)[0]
    assert "description" in text_hit.matched_fields


# ==========================================================================
# index construction
# ==========================================================================


def test_field_weights_default_to_one() -> None:
    """No invented weights. Any non-default weight must cite a measurement."""
    assert set(DEFAULT_FIELD_WEIGHTS.values()) == {1.0}


def test_each_field_is_normalised_against_its_own_average() -> None:
    index = BM25Index().build(_CORPUS)
    # Descriptions are far longer than titles; averaging them together would
    # make any described document look enormous.
    assert index.average_field_length["description"] > index.average_field_length["title"]
    # And the description average counts only documents that HAVE one.
    described = [d for d in _CORPUS if d.has_description]
    assert 0 < index.average_field_length["description"] < 100
    assert len(described) == 2


def test_index_rebuild_is_identical() -> None:
    a, b = BM25Index().build(_CORPUS), BM25Index().build(_CORPUS)
    assert a.postings.keys() == b.postings.keys()
    assert a.average_field_length == b.average_field_length


# ==========================================================================
# provenance
# ==========================================================================


def test_every_result_carries_provenance(searcher) -> None:
    for result in searcher.search("computer", limit=5):
        assert result.course_provenance is not None
        assert result.course_provenance.source_kind == "rutgers_soc"


def test_description_provenance_names_the_catalog_and_year(searcher) -> None:
    result = searcher.search("recursion", limit=1)[0]
    assert result.description_provenance is not None
    assert result.description_provenance.source_kind == "rutgers_catalog"
    assert result.description_provenance.catalog_year == "2026-2027"
    assert result.citation() == "rutgers_catalog 2026-2027"


def test_citation_is_never_fabricated() -> None:
    """A document with no recorded provenance says so rather than inventing
    a plausible-looking source."""
    bare = SearchResult(document_id="x|", course_key="01:198:111", title="T", score=1.0)
    assert bare.citation() == "unattributed"


def test_course_without_a_description_has_no_description_provenance(searcher) -> None:
    result = searcher.search("01:198:344", limit=1)[0]
    assert result.description_provenance is None
    assert result.citation() == "rutgers_soc"


# ==========================================================================
# evaluation harness
# ==========================================================================


def test_metrics_are_correct_on_a_known_example() -> None:
    """Verify the metric arithmetic itself, not the retriever."""
    outcome = QueryOutcome(
        query="q",
        query_type="t",
        retrieved=("a", "b", "c", "d", "e", "f"),
        relevant=frozenset({"c", "f", "z"}),
    )
    # 'c' at rank 3 -> 1 of 3 relevant in top 5
    assert outcome.recall_at(5) == pytest.approx(1 / 3)
    # 'c' and 'f' in top 6... but recall_at(10) sees all six retrieved
    assert outcome.recall_at(10) == pytest.approx(2 / 3)
    assert outcome.precision_at(5) == pytest.approx(1 / 5)
    assert outcome.reciprocal_rank() == pytest.approx(1 / 3)


def test_reciprocal_rank_is_zero_when_nothing_relevant_is_found() -> None:
    outcome = QueryOutcome("q", "t", ("x", "y"), frozenset({"z"}))
    assert outcome.reciprocal_rank() == 0.0
    assert outcome.recall_at(10) == 0.0


def test_evaluation_groups_by_query_type(searcher) -> None:
    queries = [
        EvalQuery("01:198:344", "exact_lookup", frozenset({"01:198:344"})),
        EvalQuery("machine learning", "conceptual", frozenset({"01:198:461"})),
    ]
    report = evaluate(searcher, queries, limit=10)
    assert set(report.by_type) == {"exact_lookup", "conceptual"}
    assert report.overall.queries == 2
    assert report.overall.mrr == pytest.approx(1.0)


# ==========================================================================
# the labelled evaluation set itself
# ==========================================================================


def test_evaluation_set_is_wellformed() -> None:
    data = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    queries = data["queries"]
    assert len(queries) >= 15

    required_types = {
        "exact_lookup",
        "conceptual",
        "synonym",
        "multi_concept",
        "ambiguous",
        "requirement_oriented",
        "description_oriented",
    }
    assert required_types <= {q["query_type"] for q in queries}

    for entry in queries:
        assert entry["query"].strip()
        assert entry["relevant"], entry["query"]
        # Every label must be a plausible Rutgers course key.
        for key in entry["relevant"]:
            assert key.count(":") == 2, (entry["query"], key)


def test_evaluation_set_documents_its_scope() -> None:
    """The set must state what it measures and what it does not, so a future
    reader cannot mistake a retrieval score for an eligibility claim."""
    data = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    about = data["_about"]
    assert "Degree Engine" in about["authority"]
    assert "relevance_scope" in about
    assert "caveat" in about


# ==========================================================================
# the architectural boundary
# ==========================================================================


def test_search_never_imports_the_degree_engine() -> None:
    """RAG retrieves and explains; the Degree Engine decides.

    An import here would be the first step toward retrieval quietly
    answering an eligibility question.
    """
    import ast
    import pathlib as _pathlib

    from app.services.search import bm25, contract, documents, evaluation

    for module in (bm25, contract, documents, evaluation):
        tree = ast.parse(_pathlib.Path(module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        # Checked against real imports, not source text: the docstrings
        # legitimately NAME the audit package when describing the boundary.
        assert not any(
            name.startswith("app.services.audit") for name in imported
        ), (module.__name__, imported)


def test_search_result_makes_no_eligibility_claim() -> None:
    """A SearchResult has no field that could carry a satisfaction verdict."""
    fields = set(SearchResult.__dataclass_fields__)
    forbidden = {"satisfied", "eligible", "counts_toward", "requirement_status"}
    assert not (fields & forbidden)


# ==========================================================================
# corpus statistics
# ==========================================================================


def test_corpus_stats_report_description_coverage() -> None:
    stats = corpus_stats(_CORPUS)
    assert stats.documents == len(_CORPUS)
    assert stats.with_description == 2
    assert "description" in stats.summary()


def test_empty_corpus_is_handled() -> None:
    assert corpus_stats([]).documents == 0
    assert build_bm25([]).search("anything", limit=5) == []


# ==========================================================================
# curated query expansion (Part E)
# ==========================================================================


def test_expansion_closes_the_measured_synonym_gap(searcher) -> None:
    """'AI' has no lexical form anywhere in the corpus; expansion reaches it."""
    from app.services.search.synonyms import ExpandingSearcher

    assert searcher.search("AI", limit=5) == [] or all(
        r.course_key != "01:198:440" for r in searcher.search("AI", limit=5)
    )
    expanded = ExpandingSearcher(searcher)
    keys = [r.course_key for r in expanded.search("AI", limit=5)]
    assert "01:198:440" in keys


def test_expansion_reports_what_it_applied(searcher) -> None:
    """A course surfacing because of an expansion must be explainable."""
    from app.services.search.synonyms import ExpandingSearcher

    expanded = ExpandingSearcher(searcher)
    expanded.search("ML", limit=5)
    assert [e.term for e in expanded.last_expansions] == ["ml"]
    assert expanded.last_expansions[0].expands_to == ("machine", "learning")


def test_expansion_keeps_the_original_terms(searcher) -> None:
    """Expansion ADDS; it never replaces, so an exact lookup can still win."""
    from app.services.search.synonyms import expand_query

    expanded, applied = expand_query("CS 344")
    assert expanded.startswith("CS 344")
    assert "computer" in expanded and "science" in expanded
    assert [e.term for e in applied] == ["cs"]


def test_expansion_does_not_fire_on_unrelated_queries() -> None:
    from app.services.search.synonyms import expand_query

    expanded, applied = expand_query("operating systems design")
    assert expanded == "operating systems design"
    assert applied == ()


def test_expansion_does_not_degrade_exact_lookup(searcher) -> None:
    """The safety property: measured, no query type regressed."""
    from app.services.search.synonyms import ExpandingSearcher

    expanded = ExpandingSearcher(searcher)
    assert expanded.search("01:198:344", limit=1)[0].course_key == "01:198:344"
    assert expanded.search("data structures", limit=1)[0].course_key == "01:198:112"


def test_every_curated_expansion_carries_a_justification() -> None:
    """Curated data, reviewed like requirement data - not a model's opinion."""
    from app.services.search.synonyms import CURATED_EXPANSIONS

    for expansion in CURATED_EXPANSIONS:
        assert expansion.note.strip(), expansion.term
        assert expansion.expands_to
        assert expansion.term == expansion.term.lower()


def test_expansion_never_maps_a_term_to_course_codes() -> None:
    """Mapping a query straight to courses would be retrieval making an
    academic judgement."""
    from app.services.search.synonyms import CURATED_EXPANSIONS

    for expansion in CURATED_EXPANSIONS:
        for word in expansion.expands_to:
            assert ":" not in word


# ==========================================================================
# RAG context assembly (Parts J, K, L)
# ==========================================================================


def _by_key():
    return {d.course_key: d for d in _CORPUS}


def test_context_is_bounded_and_deduplicated(searcher) -> None:
    from app.services.search.context import assemble_context

    context = assemble_context(searcher, "computer", _by_key(), limit=3)
    assert len(context.snippets) <= 3
    assert len(context.course_keys) == len(context.snippets)


def test_every_snippet_carries_provenance(searcher) -> None:
    from app.services.search.context import assemble_context

    context = assemble_context(searcher, "recursion", _by_key(), limit=3)
    assert context.snippets
    for snippet in context.snippets:
        assert snippet.source_kind
        assert snippet.citation() != ""
    top = context.snippets[0]
    assert top.field_name == "description"
    assert top.citation() == "rutgers_catalog 2026-2027"


def test_snippets_are_verbatim_never_rewritten(searcher) -> None:
    """Generated text must never be able to masquerade as catalog text."""
    from app.services.search.context import assemble_context

    context = assemble_context(searcher, "recursion", _by_key(), limit=1)
    source = _by_key()["01:198:112"].description
    assert context.snippets[0].text in " ".join(source.split())


def test_snippets_are_trimmed_on_a_word_boundary(searcher) -> None:
    from app.services.search.context import assemble_context

    context = assemble_context(searcher, "recursion", _by_key(), limit=1, snippet_chars=20)
    assert context.truncated
    assert len(context.snippets[0].text) <= 20
    assert not context.snippets[0].text.endswith(" ")


def test_academic_questions_are_flagged_for_the_degree_engine(searcher) -> None:
    """The boundary, machine-checked rather than merely documented."""
    from app.services.search.context import assemble_context

    for query in (
        "does CS 344 satisfy my electives",
        "how many credits do I need",
        "am I on track to graduate",
        "which courses count toward the core",
    ):
        context = assemble_context(searcher, query, _by_key(), limit=3)
        assert context.requires_degree_engine, query
        assert any("app.services.audit" in n for n in context.notes)


def test_descriptive_questions_are_not_flagged(searcher) -> None:
    from app.services.search.context import assemble_context

    context = assemble_context(searcher, "what is machine learning about", _by_key())
    assert not context.requires_degree_engine


def test_rendered_context_warns_when_the_engine_is_required(searcher) -> None:
    from app.services.search.context import assemble_context

    context = assemble_context(searcher, "do I need more credits", _by_key(), limit=2)
    assert "Degree Engine" in context.render()


def test_empty_query_yields_an_empty_context(searcher) -> None:
    from app.services.search.context import assemble_context

    context = assemble_context(searcher, "underwater basket weaving", _by_key())
    assert context.snippets == ()
    assert any("No course matched" in n for n in context.notes)


def test_context_never_dumps_the_whole_catalog(searcher) -> None:
    from app.services.search.context import DEFAULT_MAX_DOCUMENTS, assemble_context

    context = assemble_context(searcher, "computer", _by_key())
    assert len(context.snippets) <= DEFAULT_MAX_DOCUMENTS
