"""Repositories — the only code that writes SQL.

NOT IMPLEMENTED YET.

Services and API routes call repositories; they never build queries
themselves. This keeps the deterministic core testable against in-memory fakes
and keeps provenance joins in one place — it is far too easy to write an ad-hoc
query that returns a course row without the source rows that back it.
"""
