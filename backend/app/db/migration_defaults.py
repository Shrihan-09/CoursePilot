"""Compare JSON defaults without PostgreSQL's nonexistent JSON equality operator."""
import json
import re

from sqlalchemy import JSON


def _literal(value):
    match = re.fullmatch(r"'((?:[^']|'')*)'(?:::(?:json|jsonb))?", value.strip())
    if not match:
        return json.loads(value)  # Alembic may render the metadata literal unquoted.
    return json.loads(match[1].replace("''", "'"))


def compare_server_default(context, inspected_column, metadata_column,
                           inspected_default, metadata_default, rendered_metadata_default):
    if not isinstance(metadata_column.type, JSON):
        return None
    if inspected_default is None or rendered_metadata_default is None:
        return inspected_default != rendered_metadata_default
    try:
        return json.dumps(_literal(inspected_default), sort_keys=True) != json.dumps(
            _literal(rendered_metadata_default), sort_keys=True)
    except (ValueError, TypeError):
        # Nonliteral expressions remain visible as differences; never execute
        # JSON = JSON to compare them or silently suppress default drift.
        return inspected_default.strip() != rendered_metadata_default.strip()
