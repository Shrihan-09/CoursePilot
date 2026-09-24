"""Database-maintained search-corpus version (Phase 5.11).

> Any database mutation capable of changing BM25 search results also changes
> this version. Enforced at the **database boundary**.

## Why this is not the rules version

Phase 5.8 already has `rules_version`, maintained the same way. This is a
**separate counter** on purpose:

```
rules_version    requirement recuration -> the AUDIT changes
search_version   catalog/SOC ingestion  -> SEARCH RESULTS change
```

They are independent inputs. Sharing one counter would rebuild the ~264 ms
BM25 index every time a requirement was recurated - a change that cannot
affect a single search result - and would invalidate every cached audit
whenever a course title was corrected. Two questions, two counters.

## Why a database counter rather than an in-process flag

**Ingestion runs in a different process.** `coursepilot_ingestion/cli.py`
builds its own engine and writes the catalog from a separate command. An
in-memory invalidation hook in the API process cannot observe that write at
all, so the only alternatives would be to serve stale search results or to
require an API restart after every ingestion - an operational surprise.

That is the demonstrated cross-process requirement, and it is the reason
this phase persists anything at all.

## What is NOT covered

`CURATED_EXPANSIONS` is a module-level constant in `synonyms.py`, not a
table. Query expansion therefore changes only through a code deploy, and a
database counter cannot see that - so the index identity also carries a
code-side version. See `app/services/search/index_registry.py`.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, SmallInteger, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

#: Tables whose rows are read by `build_course_documents`, traced from the
#: implementation rather than assumed (Phase 5.11, Part 2):
#:
#:   course                 course_string, supplement_code, subject_code,
#:                          course_number, title, credits, level,
#:                          offering_unit_code, id
#:   subject                offering_unit_code, code, description
#:   catalog_course_entry   course_id, description, catalog_year
#:
#: Deliberately absent: every requirement/program table (they change the
#: audit, never a search result), `student`/`student_course` (not in the
#: corpus at all), and `data_source` - `course_provenance` is a hardcoded
#: literal, and the only provenance that varies is `catalog_year`, which is
#: read from `catalog_course_entry` and therefore already covered.
SEARCH_TABLES: tuple[str, ...] = (
    "course",
    "subject",
    "catalog_course_entry",
)


class SearchVersion(Base, TimestampMixin):
    """The single row PostgreSQL triggers increment. Never written by hand."""

    __tablename__ = "search_version"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, default=1, server_default=text("1")
    )
    version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1")
    )

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    def __repr__(self) -> str:
        return f"<SearchVersion {self.version}>"
