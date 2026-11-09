from types import SimpleNamespace as NS

import pytest
from sqlalchemy import JSON, Integer

from app.db.migration_defaults import compare_server_default


@pytest.mark.parametrize("stored,declared,different", [
    ("'{}'::json", "'{}'", False),
    ("'{}'::json", "{}", False),
    ("'1'::json", "true", True),
    ("'{\"b\": 2, \"a\": 1}'::json", "'{\"a\":1,\"b\":2}'", False),
    ("'[]'::json", "'{}'", True),
    (None, "'{}'", True),
    ("'{}'::json", None, True),
    ("json_build_object('a', 1)", "'{}'", True),
])
def test_json_defaults_compare_without_a_database_equality_operator(stored, declared, different):
    assert compare_server_default(None, None, NS(type=JSON()), stored, None, declared) is different


def test_other_defaults_still_use_alembics_comparator():
    assert compare_server_default(None, None, NS(type=Integer()), "1", None, "2") is None
