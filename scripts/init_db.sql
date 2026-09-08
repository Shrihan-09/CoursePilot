-- Runs once, on first initialization of the Postgres data volume.
-- Keep this file limited to extensions and database-level settings.
-- All table/schema creation belongs in Alembic migrations so that local,
-- CI, and production converge on the same migration history.

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: semantic retrieval
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- trigram similarity: fuzzy course-code / title matching
CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; -- uuid generation
