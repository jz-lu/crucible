"""Read exact, generated attack evidence; never run cryptanalysis in a web request."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from .common import MODEL, MODEL_ID, log2_int

PATH = Path(__file__).resolve().parents[1] / "data/ram_attack_curves.json"
ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATHS = frozenset({
    "crucible/attack_models/common.py", "crucible/attack_models/classical.py",
    "crucible/attack_models/lattice.py", "crucible/attack_models/codes.py",
    "scripts/build_ram_attack_notes.py",
})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


@lru_cache(maxsize=4)
def _read(path, modified, size):
    raw = Path(path).read_bytes()
    if len(raw) != size or len(raw) > 8 * 1024 * 1024:
        raise ValueError("Attack evidence exceeds the bounded catalogue size or changed during reading")
    return json.loads(raw)


@lru_cache(maxsize=4)
def _check_sources(expected, signatures):
    # Only the fixed source allowlist can be read. Stat signatures invalidate
    # this bounded cache when local development changes an accounting source.
    for relative, wanted in expected:
        content = (ROOT / relative).read_bytes()
        if len(content) > 1024 * 1024 or hashlib.sha256(content).hexdigest() != wanted:
            raise ValueError("Attack accounting source changed; regenerate the evidence")
    return True


def _verify_sources(hashes):
    if not isinstance(hashes, dict) or set(hashes) != SOURCE_PATHS:
        raise ValueError("Unrecognized attack accounting source identity")
    expected = tuple(sorted(hashes.items()))
    if any(not isinstance(value, str) or len(value) != 64 for _, value in expected):
        raise ValueError("Invalid attack accounting source hash")
    signatures = []
    for relative, _ in expected:
        stat = (ROOT / relative).stat()
        if stat.st_size > 1024 * 1024:
            raise ValueError("Attack accounting source exceeds bounded size")
        signatures.append((relative, stat.st_mtime_ns, stat.st_size))
    _check_sources(expected, tuple(signatures))


def for_release(scheme_id, version, *, path=None, expected_manifests=None):
    path = Path(path or PATH)
    if not path.exists():
        return None
    stat = path.stat()
    data = _read(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if data.get("format") != "crucible-ram-attack-catalogue-v1" or data.get("computation_model", {}).get("id") != MODEL_ID:
        raise ValueError("Unrecognized attack computation contract")
    model_digest = digest(MODEL)
    if digest(data["computation_model"]) != model_digest:
        raise ValueError("Attack computation rules do not match their current model identity")
    _verify_sources(data.get("source_sha256"))
    row = data["releases"].get(scheme_id + "@" + str(version))
    if row is None:
        return None
    value = {key: item for key, item in row.items() if key != "analysis_sha256"}
    if (row.get("analysis_sha256") != digest(value) or row.get("scheme_id") != scheme_id
            or str(row.get("version")) != str(version)):
        raise ValueError("Attack evidence identity mismatch")
    if (row.get("computation_model_sha256") != model_digest
            or row.get("source_sha256") != data["source_sha256"]):
        raise ValueError("Attack analysis uses different computation or source identities")
    expected = None
    if expected_manifests is not None:
        expected = {}
        for manifest in expected_manifests:
            identifier = manifest.get("profile_id") or manifest.get("id")
            if (not isinstance(identifier, str) or not identifier or identifier in expected
                    or manifest.get("scheme_id") != scheme_id
                    or str(manifest.get("version")) != str(version)):
                raise ValueError("Invalid expected attack profile manifest")
            expected[identifier] = digest(manifest)
    models = {m["id"]: m for m in row["models"]}
    if len(models) != len(row["models"]):
        raise ValueError("Ambiguous attack model")
    if any(m.get("computation_model_id") != MODEL_ID or m.get("unit") != "log2 bit operations" for m in models.values()):
        raise ValueError("Incompatible attack work units")
    seen = set()
    for point in row["estimates"]:
        if point["profile_id"] in seen or point["model_id"] not in models:
            raise ValueError("Ambiguous attack point")
        seen.add(point["profile_id"])
        if expected is not None and expected.get(point["profile_id"]) != point.get("manifest_sha256"):
            raise ValueError("Attack point does not match the exact registered profile")
        work, memory = int(point["work_bound"]), int(point["memory_bits"])
        if (point["computation_model_id"] != MODEL_ID or work <= 0 or memory <= 0
                or point["bits"] != log2_int(work) or point["memory_log2_bits"] != log2_int(memory)
                or sum(int(x) for x in point["breakdown"].values()) != work
                or point["success_lower_bound"] != "1/2"):
            raise ValueError("Invalid attack work account")
    if expected is not None and seen != set(expected):
        raise ValueError("Attack analysis does not cover the exact registered profiles")
    return deepcopy(row)


def presentation(row):
    """Only compact display fields cross the normal note-binding endpoint."""
    return {"format": "crucible-ram-attack-comparison-v1", "scheme_id": row["scheme_id"],
            "version": row["version"], "computation_model_id": MODEL_ID,
            "analysis_sha256": row["analysis_sha256"],
            "explanation": "Reproducible baseline work bounds for shared-key recovery with at least 50% success under the linked premises. These are not best-known attack costs or security lower bounds.",
            "models": row["models"],
            "estimates": [{key: point[key] for key in (
                "profile_id", "model_id", "bits", "work_bound", "memory_bits", "memory_log2_bits",
                "success_lower_bound", "success_status", "computation_model_id")}
                for point in row["estimates"]]}
