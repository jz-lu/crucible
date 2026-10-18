"""Measurement protocol invariants; no web app, accounts or database involved."""
import json
import sys

import pytest

from scripts import reference_benchmark as benchmark
from scripts.reference_benchmark_adapters import adapter, digest, profiles


def setup_event(**updates):
    return {"event": "setup", "duration_ns": 3, "source_sha256": "a" * 64,
            "parameters_sha256": "b" * 64, **updates}


def accept(events, value):
    events.accept(json.dumps(value))


def test_only_explicit_trusted_targets_are_importable():
    for value in ("os", "../../os", "https://example.org/code", "unknown-native"):
        with pytest.raises(ValueError):
            adapter(value)
    with pytest.raises(ValueError):
        profiles("dh", "invented")


def test_inclusive_quartiles_and_empty_summary():
    assert benchmark.summarize([])["median_ns"] is None
    assert benchmark.summarize([17])["q1_ns"] == 17
    result = benchmark.summarize([40, 10, 30, 20])
    assert (result["q1_ns"], result["median_ns"], result["q3_ns"]) == (17.5, 25, 32.5)
    assert result["samples_ns"] == [40, 10, 30, 20]
    assert digest({"z": "λ", "a": 2}) == digest({"a": 2, "z": "λ"})


@pytest.mark.parametrize("value", [
    {"event": "setup", "duration_ns": 3, "source_sha256": "a" * 64,
     "parameters_sha256": "b" * 64, "private_key": "should never be persisted"},
    {"event": "failure", "code": "untrusted diagnostic"},
    {"event": "begin", "phase": "measured", "index": 0},
    setup_event(duration_ns=True),
])
def test_invalid_worker_events_cannot_enter_evidence(value):
    with pytest.raises(ValueError):
        accept(benchmark.Events(0, 1), value)


def test_setup_identity_must_match_requested_source_and_parameters():
    events = benchmark.Events(0, 1, {"source_sha256": "c" * 64, "parameters_sha256": "b" * 64})
    with pytest.raises(ValueError):
        accept(events, setup_event())


def test_operations_and_trials_have_fixed_order_and_complete_agreement():
    events = benchmark.Events(1, 1)
    accept(events, setup_event())
    with pytest.raises(ValueError):
        accept(events, {"event": "begin", "phase": "measured", "index": 0})
    accept(events, {"event": "begin", "phase": "warmup", "index": 0})
    with pytest.raises(ValueError):
        accept(events, {"event": "operation", "phase": "warmup", "index": 0,
                        "operation": "decapsulate", "duration_ns": 2})
    with pytest.raises(ValueError):
        accept(events, {"event": "outcome", "phase": "warmup", "index": 0, "outcome": "agreement"})


def test_parent_deadline_retains_completed_operation_and_cuts_worker():
    values = [setup_event(), {"event": "begin", "phase": "measured", "index": 0},
              {"event": "operation", "phase": "measured", "index": 0,
               "operation": "keygen", "duration_ns": 19}]
    code = "import time\n" + "\n".join(f"print({json.dumps(json.dumps(value))}, flush=True)" for value in values)
    code += "\nprint('{',end='',flush=True)\ntime.sleep(30)\n"
    events, elapsed, timeout, invalid, status = benchmark.collect([sys.executable, "-I", "-B", "-c", code], 0, 1, .3)
    assert timeout and not invalid and status != 0 and elapsed < 3
    assert events.trials == [{"phase": "measured", "index": 0, "outcome": "timeout", "timings_ns": {"keygen": 19}}]


def test_untrusted_output_is_bounded_and_not_retained():
    events, elapsed, timeout, invalid, status = benchmark.collect(
        [sys.executable, "-I", "-B", "-c", "print('x'*5000,flush=True)"], 0, 1, 2)
    assert invalid and not timeout and not events.trials and elapsed < 3


@pytest.mark.parametrize("target", ["dh", "module", "frodo", "ec", "rsa"])
def test_real_small_profile_has_three_operations_and_no_secret_output(target):
    manifest = profiles(target)[0]
    result = benchmark.record(target, manifest, 1, 2, 5)
    assert result["status"] == "complete", result["notes"]
    assert result["outcomes"] == {"warmup": {"agreement": 1}, "measured": {"agreement": 2}}
    assert result["parameters_sha256"] == digest(manifest["parameters"])
    assert result["exit_code"] == 0 and not result["source_changed"]
    for operation in benchmark.OPERATIONS:
        assert result["operations"][operation]["count"] == 2
        assert all(value > 0 for value in result["operations"][operation]["samples_ns"])
    serialized = json.dumps(result)
    assert all(word not in serialized for word in ("private_key", "ciphertext_hex", "secret_hex", "seed_hex"))


def test_wrong_artifact_pin_fails_before_trial():
    manifest = profiles("module")[0]
    manifest["implementation"]["artifact_sha256"] = "0" * 64
    result = benchmark.record("module", manifest, 0, 1, 5)
    assert result["status"] == "failed"
    assert result["worker_failure"] == "identity_mismatch"
    assert not result["trials"]
