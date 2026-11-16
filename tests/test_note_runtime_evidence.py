"""A platform refresh cannot silently change the scientific account."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("note_runtime_evidence", Path(__file__).parents[1] / "deploy/note_runtime_evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def catalogue():
    return {"releases": {"example@1": {"analysis_sha256": "a" * 64,
        "estimates": [{"profile_id": "one", "manifest_sha256": "b" * 64,
                       "work_bound": "123", "success_lower_bound": "1/2", "parameters": {"n": 42}}]}}}


def test_only_exact_runtime_pins_may_change():
    old = catalogue()
    new = deepcopy(old)
    new["releases"]["example@1"]["analysis_sha256"] = "c" * 64
    new["releases"]["example@1"]["estimates"][0]["manifest_sha256"] = "d" * 64
    changes = evidence.require_unchanged_accounts(old, new)
    assert changes[0]["profile_id"] == "one"
    assert old == catalogue()


@pytest.mark.parametrize("field,value", [("work_bound", "124"), ("success_lower_bound", "1/4"), ("parameters", {"n": 43})])
def test_runtime_refresh_refuses_changed_scientific_claim(field, value):
    old = catalogue()
    new = deepcopy(old)
    new["releases"]["example@1"]["estimates"][0][field] = value
    with pytest.raises(ValueError, match="Numerical accounts"):
        evidence.require_unchanged_accounts(old, new)


def test_evidence_generation_blocks_database_connections_and_restores_methods():
    import sqlite3
    import psycopg
    from sqlalchemy.engine import Engine

    before = sqlite3.connect, Engine.connect, psycopg.connect
    with evidence.no_database_connections():
        for connect in (sqlite3.connect, sqlite3.dbapi2.connect, psycopg.connect):
            with pytest.raises(RuntimeError, match="must not open a database"):
                connect("unused")
        with pytest.raises(RuntimeError, match="must not open a database"):
            Engine.connect(None)
    assert (sqlite3.connect, Engine.connect, psycopg.connect) == before
