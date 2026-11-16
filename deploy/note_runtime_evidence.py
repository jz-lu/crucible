#!/usr/bin/env python3
"""Regenerate analytical note evidence in the pinned runtime, without publishing.

Run inside the reviewed Linux image with the candidate source mounted read-only.
Only the separate output directory is written. No application, database, mail,
model API, registry credential or hosted account is used by this process.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import re
import sys


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def require_unchanged_accounts(before, after):
    """Only runtime-manifest pins and their enclosing hashes may differ."""
    original, generated = deepcopy(before), deepcopy(after)
    changes = []
    if set(original["releases"]) != set(generated["releases"]):
        raise ValueError("The analytical release set changed")
    for identity, row in original["releases"].items():
        replacement = generated["releases"][identity]
        row.pop("analysis_sha256")
        replacement.pop("analysis_sha256")
        if len(row["estimates"]) != len(replacement["estimates"]):
            raise ValueError("The analytical configuration count changed")
        for old, new in zip(row["estimates"], replacement["estimates"]):
            old_pin, new_pin = old.pop("manifest_sha256"), new.pop("manifest_sha256")
            if not all(re.fullmatch(r"[0-9a-f]{64}", pin) for pin in (old_pin, new_pin)):
                raise ValueError("Invalid runtime-manifest pin")
            if old_pin != new_pin:
                changes.append({"release": identity, "profile_id": old["profile_id"],
                                "previous_manifest_sha256": old_pin, "manifest_sha256": new_pin})
    if original != generated:
        raise ValueError("Numerical accounts, parameters, assumptions or model evidence changed")
    return changes


@contextmanager
def no_database_connections():
    """Allow model declarations, but fail before any database connection."""
    import sqlite3
    from sqlalchemy.engine import Engine
    import psycopg

    def refused(*args, **kwargs):
        raise RuntimeError("Scientific evidence generation must not open a database")

    boundaries = [(Engine, "connect"), (Engine, "raw_connection"),
                  (sqlite3, "connect"), (sqlite3.dbapi2, "connect"), (psycopg, "connect")]
    original = [(target, name, getattr(target, name)) for target, name in boundaries]
    try:
        for target, name in boundaries:
            setattr(target, name, refused)
        yield
    finally:
        for target, name, value in reversed(original):
            setattr(target, name, value)


def generate(source, output, revision):
    source, output = source.resolve(), output.resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("An exact source commit is required")
    if output == source or output.is_relative_to(source):
        raise ValueError("Evidence output must be separate from the read-only source")
    if any(output.iterdir()):
        raise ValueError("Use an empty evidence output directory")
    contract = json.loads((source / "deploy/release-runtime.json").read_text())
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise ValueError("Run this evidence builder in the reviewed Linux AMD64 image")
    if platform.python_version() != contract["python_version"]:
        raise ValueError("The interpreter does not match the reviewed runtime")

    # Use the pinned image's actual implementation and native files. The new
    # analytical modules are loaded only after all runtime identities validate.
    sys.path.insert(0, "/opt/crucible")
    import crucible
    from crucible import registry
    if Path(registry.__file__).resolve() != Path("/opt/crucible/crucible/registry.py"):
        raise ValueError("Unexpected registry implementation")
    registry.validate_registry()
    expected = {(row["scheme_id"], row["version"]): row for row in contract["releases"]}
    if set(registry.ADAPTERS) != set(expected):
        raise ValueError("The registered release set changed")
    count = 0
    for identity, row in expected.items():
        manifests = registry.adapter_for(*identity).manifests()
        if len(manifests) != row["profiles"] or digest(manifests) != row["manifests_sha256"]:
            raise ValueError("The runtime does not match its reviewed manifest contract")
        count += len(manifests)
    if len(expected) != 32 or count != 1466:
        raise ValueError("Unexpected reviewed runtime inventory")

    crucible.__path__.insert(0, str(source / "crucible"))
    builder_path = source / "scripts/build_ram_attack_notes.py"
    spec = importlib.util.spec_from_file_location("crucible_note_evidence_builder", builder_path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    original = json.loads((source / "crucible/data/ram_attack_curves.json").read_text())
    builder.PRESENTATIONS = output / "scheme_note_presentations.json"
    builder.PRESENTATIONS.write_bytes((source / "crucible/data/scheme_note_presentations.json").read_bytes())
    builder.OUTPUT = output / "ram_attack_curves.json"
    builder.build()
    regenerated = json.loads(builder.OUTPUT.read_text())
    changes = require_unchanged_accounts(original, regenerated)
    from crucible.attack_models import catalogue
    points = 0
    for identity in regenerated["releases"]:
        scheme, version = identity.split("@", 1)
        checked = catalogue.for_release(scheme, version, path=builder.OUTPUT,
            expected_manifests=registry.adapter_for(scheme, version).manifests())
        if not checked:
            raise ValueError("The generated exact release did not validate")
        points += len(checked["estimates"])
    # Canonical Statement declarations import db.Base; that does not initialize
    # application state. The surrounding connection guard checks the actual I/O
    # boundary instead of confusing a data-model import with a database write.
    if {"crucible.app", "crucible.config"} & sys.modules.keys():
        raise ValueError("Evidence generation unexpectedly initialized application state")
    receipt = {"format": "crucible-note-runtime-evidence-v1", "source_revision": revision,
        "runtime_image": contract["app_base"], "runtime_config": contract["app_base_config"],
        "python": platform.python_version(), "registered_releases": len(expected),
        "registered_profiles": count, "analytical_releases": len(regenerated["releases"]),
        "analytical_accounts": points, "numerical_evidence_unchanged": True,
        "runtime_pin_changes": changes, "database_access": False, "deployment_performed": False,
        "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (builder.OUTPUT, builder.PRESENTATIONS)},
        "builder_sha256": hashlib.sha256(builder_path.read_bytes()).hexdigest()}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({key: receipt[key] for key in ("source_revision", "analytical_accounts",
          "numerical_evidence_unchanged", "deployment_performed")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    with no_database_connections():
        generate(args.source, args.output, args.revision)
