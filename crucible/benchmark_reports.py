"""Read explicitly published local measurements; never run a benchmark or intake code.

Publication hashes are an operator allowlist, not proof of truthful measurement.
Current adapter identity and raw trial receipts determine what can be displayed.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import statistics
from uuid import UUID

from .reference_policy import METHOD

DATA_DIR = Path(__file__).resolve().parent / "data" / "reference_benchmarks"
OPERATIONS = ("keygen", "encapsulate", "decapsulate")
OUTCOMES = {"agreement", "agreement_failure", "abort", "error", "timeout"}
MAX_REPORT = 4 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")
NATIVE_SCHEMES = {"hqc": "hqc", "hqc-v3": "hqc", "mceliece": "classic-mceliece", "ntru": "sntrup", "mlkem": "ml-kem"}


def _require(value):
    if not value:
        raise ValueError("Invalid or stale benchmark publication")


def _text(value, limit=1000):
    _require(type(value) is str and 0 < len(value) <= limit and bool(value.strip()))
    return value


def _integer(value, maximum):
    _require(type(value) is int and 0 <= value <= maximum)
    return value


def _finite(value, maximum=3600):
    _require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum)
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


@lru_cache(maxsize=24)
def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _signature(path):
    item = path.lstat()
    _require(stat.S_ISREG(item.st_mode))  # No symlink publication or source files.
    return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns


@lru_cache(maxsize=24)
def _read_cached(path, signature, limit):
    _require(signature[2] <= limit)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        item = os.fstat(stream.fileno())
        _require((item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns) == signature)
        raw = stream.read(limit + 1)
    _require(len(raw) <= limit and _signature(Path(path)) == signature)
    return raw


def _read(path, limit):
    signature = _signature(path)
    raw = _read_cached(str(path), signature, limit)
    _require(_signature(path) == signature)
    return raw


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


@lru_cache(maxsize=16)
def _document(raw):
    def nonfinite(_):
        raise ValueError("Nonfinite JSON")
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=nonfinite)
    _require(type(value) is dict)
    return value


def _publication(scheme_id, version):
    index = _document(_read(DATA_DIR / "index.json", 65536))
    _require(set(index) == {"format", "reports"} and index["format"] == "crucible-benchmark-publications-v1")
    _require(type(index["reports"]) is list and len(index["reports"]) <= 64)
    found, identities = None, set()
    for entry in index["reports"]:
        _require(type(entry) is dict and set(entry) == {"scheme_id", "version", "file", "sha256", "status"})
        identity = _text(entry["scheme_id"], 100), _text(entry["version"], 100)
        _require(identity not in identities)
        identities.add(identity)
        name = _text(entry["file"], 128)
        _require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.json", name) and name != "index.json")
        _require(HEX.fullmatch(entry["sha256"]) and entry["status"] == "local-pilot")
        if identity == (scheme_id, version):
            found = entry
    return found


def _trials(row, policy):
    limits = {"warmup": _integer(row["warmup_requested"], 10),
              "measured": _integer(row["requested_trials"], 1000)}
    _require(limits["measured"] >= 1 and limits["warmup"] == policy["warmups"]
             and limits["measured"] == policy["trials"])
    trials = row["trials"]
    _require(type(trials) is list and len(trials) <= sum(limits.values()))
    expected = [(phase, i) for phase in ("warmup", "measured") for i in range(limits[phase])]
    counts = {phase: Counter() for phase in limits}
    samples = {operation: [] for operation in OPERATIONS}
    for position, trial in enumerate(trials):
        _require(type(trial) is dict and set(trial) == {"phase", "index", "outcome", "timings_ns"})
        phase, index, outcome = trial["phase"], trial["index"], trial["outcome"]
        _require(type(index) is int and (phase, index) == expected[position] and outcome in OUTCOMES)
        timings = trial["timings_ns"]
        _require(type(timings) is dict and set(timings) == set(OPERATIONS[:len(timings)]))
        _require(outcome not in {"agreement", "agreement_failure"} or len(timings) == 3)
        _require(outcome != "timeout" or position == len(trials) - 1)
        for operation, duration in timings.items():
            _integer(duration, 120_000_000_000)
            if phase == "measured":
                samples[operation].append(duration)
        counts[phase][outcome] += 1
    declared = row["outcomes"]
    _require(type(declared) is dict and set(declared) == set(counts))
    for phase, tally in declared.items():
        _require(type(tally) is dict and set(tally) <= OUTCOMES)
        for count in tally.values():
            _integer(count, limits[phase])
    _require(declared == {phase: dict(counts[phase]) for phase in counts})
    for phase, field in (("warmup", "warmup_completed"), ("measured", "completed_trials")):
        _require(_integer(row[field], limits[phase]) == sum(counts[phase][v] for v in ("agreement", "agreement_failure")))
    failures = sum(sum(count for outcome, count in tally.items() if outcome != "agreement") for tally in counts.values())
    return samples, counts, limits, failures


def _statistics(samples):
    if not samples:
        return None, None, None
    q1, _, q3 = statistics.quantiles(samples, n=4, method="inclusive") if len(samples) > 1 else (samples[0],) * 3
    return statistics.median(samples), q1, q3


def _status(row, counts, limits):
    for key in ("timed_out", "protocol_error", "source_changed"):
        _require(type(row[key]) is bool)
    _require(row["worker_failure"] in (None, "identity_mismatch", "worker_error"))
    _require(row["exit_code"] is None or type(row["exit_code"]) is int and -255 <= row["exit_code"] <= 255)
    failed = (row["protocol_error"] or row["source_changed"] or row["worker_failure"] is not None
              or any(counts[p][o] for p in counts for o in ("error", "agreement_failure")))
    incomplete = (row["timed_out"] or row["exit_code"] != 0
                  or any(counts[p]["agreement"] != limits[p] for p in counts))
    _require(not any(counts[p]["timeout"] for p in counts) or row["timed_out"])
    _require(row["status"] == ("failed" if failed else "partial" if incomplete else "complete"))
    if failed:
        return "failed"
    if row["timed_out"] or any(counts[p]["timeout"] for p in counts):
        return "timeout"
    return "partial" if incomplete else "measured"


def _sample_scope(row, status, counts, limits):
    """Distinguish an incomplete run from a completed run with bounded keygen aborts.

    A zero-operation abort happened before key generation returned. Only when
    all planned trials ran, and every other trial agreed, do all recorded
    operation samples describe exchanges conditioned on keygen succeeding.
    This does not measure aborted-attempt latency or prove an abort rate.
    """
    if status == "measured":
        return "complete-run"
    if (status == "partial" and row["exit_code"] == 0
            and not row["timed_out"]
            and all(sum(counts[phase].values()) == limits[phase] for phase in limits)
            and all(set(counts[phase]) <= {"agreement", "abort"} for phase in limits)
            and any(counts[phase]["abort"] for phase in limits)
            and counts["measured"]["agreement"] > 0
            and all(not trial["timings_ns"] for trial in row["trials"]
                    if trial["outcome"] == "abort")):
        return "successful-key-generation"
    return "partial-operations"


def _native_artifact(row, harness):
    """Validate an operator-published build receipt without executing its files."""
    artifact = row["benchmark_artifact"]
    fields = {"format", "target", "scheme_id", "scheme_version", "profile_id", "parameters_sha256",
              "adapter_artifact_sha256", "compiler", "build_options", "files", "measurement_scope",
              "binary_sha256", "sha256"}
    _require(type(artifact) is dict and set(artifact) == fields)
    _require(artifact["format"] == "crucible-native-benchmark-artifact-v1"
             and NATIVE_SCHEMES.get(artifact["target"]) == row["scheme_id"])
    if artifact["target"] == "hqc-v3":
        _require(row["version"] == "modeled-research-v3")
    _require((artifact["scheme_id"], artifact["scheme_version"], artifact["profile_id"],
              artifact["parameters_sha256"], artifact["adapter_artifact_sha256"]) ==
             (row["scheme_id"], row["version"], row["profile_id"], row["parameters_sha256"], row["artifact_sha256"]))
    for key in ("parameters_sha256", "adapter_artifact_sha256", "binary_sha256", "sha256"):
        _require(type(artifact[key]) is str and HEX.fullmatch(artifact[key]))
    _require(artifact["sha256"] == _digest({k: v for k, v in artifact.items() if k != "sha256"}))
    _text(artifact["compiler"], 300)
    _text(artifact["measurement_scope"], 1000)
    options = artifact["build_options"]
    _require(type(options) is list and 1 <= len(options) <= 32)
    for option in options:
        _text(option, 200)
    files = artifact["files"]
    _require(type(files) is dict and 2 <= len(files) <= 512)
    for name, value in files.items():
        _text(name, 240)
        _require(re.fullmatch(r"[A-Za-z0-9_.+/-]+", name) and not name.startswith("/")
                 and all(part not in ("", ".", "..") for part in name.split("/"))
                 and type(value) is str and HEX.fullmatch(value))
    _require(files["driver/reference_benchmark_native.c"] == harness["files"]["scripts/reference_benchmark_native.c"])
    _require(any(name.startswith("libraries/") for name in files))
    return artifact


@lru_cache(maxsize=16)
def _summarize(raw, scheme_id, version, bindings):
    report = _document(raw)
    _require(report["format"] == "crucible-reference-benchmark-v1" and report["method_id"] == METHOD["id"])
    _require(str(UUID(report["run_id"])) == report["run_id"])
    measured_at = _text(report["measured_at"], 64)
    _require(datetime.fromisoformat(measured_at).utcoffset() is not None)
    policy = report["policy"]
    _integer(policy["warmups"], 10)
    _require(1 <= _integer(policy["trials"], 1000))
    _require(1 <= _finite(policy["profile_timeout_seconds"], 120))
    harness = report["harness"]
    _require(type(harness["files"]) is dict and 1 <= len(harness["files"]) <= 32
             and all(HEX.fullmatch(value) for value in harness["files"].values())
             and harness["sha256"] == _digest(harness["files"]))
    rows = [row for row in report["records"] if (row["scheme_id"], row["version"]) == (scheme_id, version)]
    _require(len(rows) == len(bindings) and bool(rows))
    results, notes, implementation, native_builds, compiler = [], [], None, [], None
    for row, binding in zip(rows, bindings):
        profile_id, parameters_hash, source_hash, artifact_hash = binding
        _require((row["profile_id"], row["parameters_sha256"], row["source_sha256"], row["artifact_sha256"]) == binding)
        identity = {key: _text(row["implementation"][key], 300) for key in ("label", "language", "performance_level")}
        if identity["language"] == "Python":
            _require(identity["performance_level"] == "reference" and row["benchmark_artifact"] is None)
        else:
            _require(identity["language"] == "C")
            artifact = _native_artifact(row, harness)
            _require(identity["performance_level"] == ("architecture-optimized" if artifact["target"] == "mlkem" else "reference"))
            _require(compiler is None or compiler == artifact["compiler"])
            compiler = artifact["compiler"]
            native_builds.append({"profile_id": profile_id, "artifact_sha256": artifact["sha256"],
                                  "binary_sha256": artifact["binary_sha256"], "build_options": artifact["build_options"]})
            notes.append(artifact["measurement_scope"])
        identity.update(source_sha256=source_hash, artifact_sha256=artifact_hash)
        _require(implementation is None or implementation == identity)
        implementation = identity
        if row["setup_seconds"] is not None:
            _finite(row["setup_seconds"])
        _finite(row["end_to_end_seconds"])
        samples, counts, limits, failures = _trials(row, policy)
        status = _status(row, counts, limits)
        sample_scope = _sample_scope(row, status, counts, limits)
        outcomes = {phase: {outcome: counts[phase][outcome] for outcome in sorted(OUTCOMES)}
                    for phase in limits}
        _require(type(row["operations"]) is dict and set(row["operations"]) == set(OPERATIONS))
        for operation in OPERATIONS:
            values, stored = samples[operation], row["operations"][operation]
            median, q1, q3 = _statistics(values)
            expected = {"count": len(values), "samples_ns": values, "median_ns": median,
                        "q1_ns": q1, "q3_ns": q3, "min_ns": min(values) if values else None,
                        "max_ns": max(values) if values else None}
            _require(stored == expected and type(stored["count"]) is int)
            _require(type(stored["samples_ns"]) is list)
            for value in stored["samples_ns"]:
                _integer(value, 120_000_000_000)
            for key in ("median_ns", "q1_ns", "q3_ns", "min_ns", "max_ns"):
                if stored[key] is not None:
                    _finite(stored[key], 120_000_000_000)
            results.append({"profile_id": profile_id, "operation": operation, "status": status,
                "sample_scope": sample_scope, "outcomes": deepcopy(outcomes),
                "samples": len(values), "median_seconds": None if median is None else median / 1e9,
                "q1_seconds": None if q1 is None else q1 / 1e9, "q3_seconds": None if q3 is None else q3 / 1e9,
                "failure_count": failures})
        _require(type(row["notes"]) is list and len(row["notes"]) <= 32)
        notes.extend(_text(note, 1000) for note in row["notes"])
    environment = report["environment"]
    _require(type(environment["single_threaded"]) is bool and environment["single_threaded"])
    return {"id": report["run_id"], "scheme_id": scheme_id, "scheme_version": version,
        "status": "local-pilot", "measured_at": measured_at, "method_id": METHOD["id"],
        "implementation": implementation, "native_builds": native_builds,
        "sampling": {"warmups": policy["warmups"], "trials": policy["trials"],
                     "profile_timeout_seconds": policy["profile_timeout_seconds"]},
        "environment": {"cpu": _text(environment["cpu_model"], 300), "os": _text(environment["os"], 300),
            "runtime": (("Native executable; harness " if native_builds else "") + _text(environment["python"], 300)),
            "compiler": compiler or "Not applicable (Python)", "threads": 1,
            "cpu_affinity": _text(environment["cpu_affinity"], 300)},
        "results": results, "notes": list(dict.fromkeys(notes)) + [
            "Raw completed operations and outcomes are retained. Failure counts include warmup and measured trials, with resource aborts distinguished from disagreement and implementation errors.",
            "Successful-key-generation samples are conditional timings: every planned trial ran, aborted key generation returned no key, and every completed exchange agreed. Their medians exclude aborted attempts, whose durations were not recorded; they are not total attempt latency or complete-run measurements.",
            "Local reference measurements, not intrinsic scheme costs or a security assessment."]}


def report_for(adapter):
    """Return a defensive summary copy, or None for absent, invalid or stale evidence."""
    try:
        scheme_id, version = adapter.SCHEME_ID, adapter.SCHEME_VERSION
        entry = _publication(scheme_id, version)
        if entry is None:
            return None
        raw = _read(DATA_DIR / entry["file"], MAX_REPORT)
        _require(_sha(raw) == entry["sha256"])
        document = _document(raw)
        _require(type(document["records"]) is list and len(document["records"]) <= 256)
        rows = [row for row in document["records"] if (row["scheme_id"], row["version"]) == (scheme_id, version)]
        _require(bool(rows))
        source_hash = _sha(_read(Path(adapter.__file__), MAX_REPORT))
        _require(source_hash == adapter.IMPLEMENTATION_SHA256)
        bindings, seen = [], set()
        for row in rows:
            profile_id = _text(row["profile_id"], 100)
            _require(profile_id not in seen)
            seen.add(profile_id)
            manifest = adapter.get_manifest(profile_id)  # Metadata only; never generate or verify_artifact.
            _require((manifest["scheme_id"], manifest["version"], manifest["id"], manifest["profile_id"])
                     == (scheme_id, version, profile_id, profile_id))
            _require(manifest["implementation"]["source_sha256"] == source_hash)
            artifact = manifest["implementation"].get("artifact_sha256")
            _require(artifact is None or HEX.fullmatch(artifact))
            bindings.append((profile_id, _digest(manifest["parameters"]), source_hash, artifact))
        return deepcopy(_summarize(raw, scheme_id, version, tuple(bindings)))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, RuntimeError):
        return None
