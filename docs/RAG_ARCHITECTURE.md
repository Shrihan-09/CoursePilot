# Retrieval Architecture

Status: **design only.** No retriever, embedding, or index is implemented.
`app/services/retrieval/` defines the interface and nothing else.

---

## 1. The core commitment

> We will **measure** which retrieval strategy works, not assume it.

Semantic search is frequently assumed to be the better option. For this corpus
that assumption is doubtful in specific, predictable ways:

- **"CS 112"** is an exact-match query. Embeddings are poor at exact
  identifier matching; BM25 is excellent at it.
- **"courses about machine learning"** is conceptual. BM25 misses synonyms;
  embeddings handle it well.
- **"data structures prerequisite"** needs both.

Since real student queries will be a mix, the architecture keeps all
strategies behind one interface and compares them on a labeled eval set.

---

## 2. What retrieval returns

`RetrievedChunk` carries `entity_type` and `entity_id` — **pointers into the
normalized database**, not free-floating text.

This matters more than it looks. If retrieval returned only text, the planner
would be reasoning over prose, and its output would be prose-derived claims
that cannot be checked. By returning entity ids, retrieval hands the planner a
menu of *real database rows*, and the validator can later verify every one.

Retrieval narrows the search space. It is not the source of truth.

---

## 3. Corpus

`document_chunk` holds chunked text derived from authoritative rows, with both
a `vector` column and a `tsvector` column. One table, two indexes.

Both retrievers read the same table by design — comparing retrievers over
different corpora measures the corpora, not the retrievers.

### Chunking

Rutgers records are mostly short and already structured. Proposed:

| Entity | Chunking |
|---|---|
| Course | One chunk: code + title + description + subject. Rarely long enough to split. |
| Requirement | One chunk per requirement node, with parent context prepended |
| Program | One chunk per program version summary |
| Section | Not chunked — sections are queried by structured filters, never semantically |

Sections are excluded on purpose. Nobody searches "a section that meets on
Tuesday" semantically; that is a SQL `WHERE` clause. Embedding structured data
that is only ever filtered wastes storage and dilutes the corpus.

---

## 4. The four strategies

### 4.1 BM25 (keyword)

Postgres full-text search over `tsvector` with a GIN index.

Strong on: course codes, exact titles, subject abbreviations, rare terms.
Weak on: synonyms, paraphrase, conceptual queries.

Postgres FTS rather than a separate Elasticsearch: one datastore, transactional
consistency with the authoritative rows, and no sync pipeline. If ranking
quality proves insufficient, revisit.

### 4.2 Vector (semantic)

pgvector, cosine distance, HNSW index.

Strong on: conceptual and paraphrased queries.
Weak on: exact identifiers, negation, numeric constraints.

**Open decision:** the embedding model is not chosen. It fixes
`EMBEDDING_DIM`, and changing it later means re-embedding everything.

### 4.3 Hybrid

Run both, fuse the rankings. Proposed: **Reciprocal Rank Fusion**.

```
score(d) = Σ  1 / (k + rank_i(d))
```

RRF over score normalization because BM25 scores and cosine similarities are
not on comparable scales, and normalizing them requires tuning constants that
drift with the corpus. RRF uses only ranks, so it needs no calibration.

### 4.4 Reranking

A cross-encoder (or LLM) rescores the top ~50 fused candidates down to ~10.

Considerably more accurate and considerably more expensive per query. Whether
it earns its cost is exactly the kind of question the eval set exists to
answer. `RERANKER_ENABLED` defaults to `false`.

---

## 5. Hard filters are not ranking signals

Term, program, campus, and active status are applied as **SQL predicates**
before ranking.

A course from the wrong term is wrong no matter how well it matches the query.
Treating term as a soft ranking signal lets a beautifully-matching but
unoffered course outrank a correct one. `Retriever.retrieve(filters=...)` is
where these live.

---

## 6. Evaluation

### 6.1 The eval set

A labeled set of realistic student queries with known-correct results, stored
in `tests/fixtures/`. Must cover:

- exact code lookups (`"CS 112"`)
- title lookups
- conceptual queries (`"classes about databases"`)
- requirement queries
- prerequisite questions
- misspellings
- queries with **no correct answer** — the retriever must return nothing
  rather than the least-bad match

That last category is routinely omitted and routinely important. A retriever
that always returns something teaches the planner to always propose something.

### 6.2 Metrics

| Metric | Why |
|---|---|
| Recall@k | Did the right answer make it into the candidate set at all? The planner cannot select what retrieval never returned. **The primary metric.** |
| MRR | How high did it rank? |
| nDCG@10 | Graded relevance |
| Precision@5 | Noise in the top results |
| Latency p50/p95 | Interactive budget |
| Cost/query | Reranking especially |

Recall@k is primary because retrieval is a **filter feeding a planner**, not a
final answer. A correct result at rank 8 is fine; a correct result absent from
the top 50 is fatal.

### 6.3 Procedure

1. Freeze the corpus and the eval set.
2. Run each of bm25 / vector / hybrid / hybrid+rerank.
3. Report per-metric and **per query category** — the aggregate winner may lose
   badly on code lookups, which are common enough to matter on their own.
4. Pick a default; keep the rest available via `RETRIEVAL_STRATEGY`.
5. Re-run when the corpus or the embedding model changes.

### 6.4 The expected outcome

Hybrid will probably win overall, with BM25 winning code lookups outright.
**That is a hypothesis, and it gets written down before the measurement so the
result cannot be rationalized after the fact.**

---

## 7. Authoritative data wins

For academic questions, retrieval draws from ingested Rutgers records only.
General model knowledge about "typical CS curricula" is not a source and must
never fill a gap.

If retrieval returns nothing, the correct response is "I don't have
authoritative data on that," not an answer assembled from priors.

---

## 8. Not implemented

Chunking · embedding generation · either index · fusion · reranking · the eval
set · the query interface.

Contract only: `app/services/retrieval/__init__.py`.
