"""Published timing evidence must match raw receipts and the exact current adapter."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from crucible import benchmark_reports as reports
from scripts.reference_benchmark import summarize
from scripts.reference_benchmark_adapters import digest


def refresh(row):
    row["outcomes"] = {phase: dict(Counter(trial["outcome"] for trial in row["trials"]
                                         if trial["phase"] == phase)) for phase in ("warmup", "measured")}
    for phase, key in (("warmup", "warmup_completed"), ("measured", "completed_trials")):
        row[key] = sum(row["outcomes"][phase].get(outcome, 0) for outcome in ("agreement", "agreement_failure"))
    row["operations"] = {operation: summarize([trial["timings_ns"][operation] for trial in row["trials"]
        if trial["phase"] == "measured" and operation in trial["timings_ns"]]) for operation in reports.OPERATIONS}


@pytest.fixture
def publication(tmp_path, monkeypatch):
    directory = tmp_path / "publications"
    directory.mkdir()
    monkeypatch.setattr(reports, "DATA_DIR", directory)
    source = tmp_path / "adapter.py"
    source.write_text("Synthetic trusted adapter source\n")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = {"id": "example-1", "profile_id": "example-1", "scheme_id": "example", "version": "1",
        "parameters": {"dimension": 4}, "implementation": {"source_sha256": source_hash}}
    def no_execution(*_):
        pytest.fail("The report reader must never execute an algorithm or artifact verifier")
    adapter = SimpleNamespace(SCHEME_ID="example", SCHEME_VERSION="1", IMPLEMENTATION_SHA256=source_hash,
        __file__=str(source), get_manifest=lambda identifier: deepcopy(manifest),
        generate=no_execution, verify_artifact=no_execution)
    row = {"scheme_id": "example", "version": "1", "profile_id": "example-1",
        "parameters_sha256": digest(manifest["parameters"]), "source_sha256": source_hash,
        "artifact_sha256": None, "benchmark_artifact": None,
        "implementation": {"label": "Synthetic reference", "language": "Python", "performance_level": "reference"},
        "requested_trials": 4, "warmup_requested": 1, "status": "complete", "setup_seconds": .05,
        "end_to_end_seconds": 1.0, "timed_out": False, "protocol_error": False, "source_changed": False,
        "worker_failure": None, "exit_code": 0, "notes": [], "trials": []}
    for phase, values in (("warmup", [99_000_000]), ("measured", [1_000_000, 2_000_000, 3_000_000, 4_000_000])):
        for i, value in enumerate(values):
            row["trials"].append({"phase": phase, "index": i, "outcome": "agreement",
                                  "timings_ns": {operation: value for operation in reports.OPERATIONS}})
    refresh(row)
    files = {"scripts/fixture.py": "a" * 64}
    data = {"format": "crucible-reference-benchmark-v1", "method_id": reports.METHOD["id"],
        "run_id": "6c20d31d-12b5-49b5-b940-e81c66d1d282", "measured_at": "2026-10-17T12:00:00+00:00",
        "environment": {"cpu_model": "Synthetic CPU", "os": "Synthetic OS", "python": "CPython test",
                        "single_threaded": True, "cpu_affinity": "not pinned"}, "harness": {"files": files, "sha256": digest(files)},
        "policy": {"warmups": 1, "trials": 4, "profile_timeout_seconds": 30}, "records": [row]}
    def publish(value=data):
        raw = json.dumps(value, sort_keys=True).encode()
        (directory / "pilot.json").write_bytes(raw)
        index = {"format": "crucible-benchmark-publications-v1", "reports": [{"scheme_id": adapter.SCHEME_ID,
            "version": adapter.SCHEME_VERSION, "file": "pilot.json", "sha256": hashlib.sha256(raw).hexdigest(), "status": "local-pilot"}]}
        (directory / "index.json").write_text(json.dumps(index))
        return index
    publish()
    return adapter, data, manifest, directory, publish


def test_summary_recomputes_inclusive_quartiles_and_does_not_expose_raw_data(publication):
    adapter, _, _, _, _ = publication
    report = reports.report_for(adapter)
    assert report["status"] == "local-pilot" and report["implementation"]["artifact_sha256"] is None
    assert report["sampling"] == {"warmups": 1, "trials": 4, "profile_timeout_seconds": 30}
    assert report["environment"]["cpu_affinity"] == "not pinned"
    assert len(report["results"]) == 3
    for row in report["results"]:
        assert row["status"] == "measured" and row["samples"] == 4 and row["failure_count"] == 0
        assert row["sample_scope"] == "complete-run"
        assert row["outcomes"]["measured"] == {"agreement": 4, "abort": 0, "error": 0,
                                                  "agreement_failure": 0, "timeout": 0}
        assert row["median_seconds"] == pytest.approx(.0025)
        assert row["q1_seconds"] == pytest.approx(.00175)
        assert row["q3_seconds"] == pytest.approx(.00325)
    assert "trials" not in report and "harness" not in report
    report["results"][0]["samples"] = 999
    assert reports.report_for(adapter)["results"][0]["samples"] == 4


@pytest.mark.parametrize("failure", ["summary", "samples", "count", "outcomes", "bool-count",
    "bool-duration", "negative-duration", "nan", "infinity", "out-of-order", "duplicate-profile", "method", "hash",
    "parameters", "artifact", "source", "harness", "missing-termination", "claimed-complete", "native", "native-label"])
def test_invalid_or_stale_reports_are_gaps_not_measurements(publication, failure):
    adapter, data, manifest, _, publish = publication
    row = data["records"][0]
    if failure == "summary": row["operations"]["keygen"]["median_ns"] += 1
    elif failure == "samples": row["operations"]["keygen"]["samples_ns"][0] += 1
    elif failure == "count": row["completed_trials"] = 5
    elif failure == "outcomes": row["outcomes"]["measured"]["agreement"] = -1
    elif failure == "bool-count": row["outcomes"]["warmup"]["agreement"] = True
    elif failure == "bool-duration": row["trials"][1]["timings_ns"]["keygen"] = True
    elif failure == "negative-duration": row["trials"][1]["timings_ns"]["keygen"] = -1
    elif failure == "nan": row["end_to_end_seconds"] = float("nan")
    elif failure == "infinity": row["setup_seconds"] = float("inf")
    elif failure == "out-of-order": row["trials"][1]["index"] = 1
    elif failure == "duplicate-profile": data["records"].append(deepcopy(row))
    elif failure == "method": data["method_id"] = "unknown-method"
    elif failure == "hash": row["source_sha256"] = "b" * 64
    elif failure == "parameters": manifest["parameters"]["dimension"] = 8
    elif failure == "artifact": manifest["implementation"]["artifact_sha256"] = "a" * 64
    elif failure == "source": adapter.IMPLEMENTATION_SHA256 = "b" * 64
    elif failure == "harness": data["harness"]["sha256"] = "b" * 64
    elif failure == "missing-termination": row.pop("timed_out")
    elif failure == "native": row["benchmark_artifact"] = {"compiler": "unreviewed driver"}
    elif failure == "native-label": row["implementation"]["language"] = "C"
    elif failure == "claimed-complete":
        row["trials"].pop()
        refresh(row)
    publish()
    assert reports.report_for(adapter) is None


@pytest.mark.parametrize("outcome,status", [("agreement_failure", "failed"), ("error", "failed"),
                                            ("abort", "partial"), ("timeout", "timeout")])
def test_unsuccessful_trials_retain_completed_samples_without_becoming_graph_points(publication, outcome, status):
    adapter, data, _, _, publish = publication
    row = data["records"][0]
    row["trials"] = row["trials"][:3]
    row["trials"][-1]["outcome"] = outcome
    if outcome != "agreement_failure":
        row["trials"][-1]["timings_ns"].pop("decapsulate")
    row.update(status="failed" if status == "failed" else "partial", timed_out=outcome == "timeout",
               exit_code=-9 if outcome == "timeout" else 1 if status == "failed" else 0)
    refresh(row)
    publish()
    result = reports.report_for(adapter)
    assert result is not None
    assert all(item["status"] == status and item["failure_count"] == 1 for item in result["results"])
    assert all(item["sample_scope"] == "partial-operations" for item in result["results"])
    assert result["results"][0]["samples"] == 2
    assert result["results"][2]["samples"] == (2 if outcome == "agreement_failure" else 1)


@pytest.mark.parametrize("phase,index,expected_samples,expected_median", [
    ("warmup", 0, 4, .0025), ("measured", 1, 3, .003),
])
def test_completed_run_with_keygen_abort_exposes_conditional_medians_and_exact_outcomes(
        publication, phase, index, expected_samples, expected_median):
    adapter, data, _, directory, publish = publication
    row = data["records"][0]
    row["trials"][index].update(outcome="abort", timings_ns={})
    row["status"] = "partial"
    refresh(row)
    publish()
    original = (directory / "pilot.json").read_bytes()
    report = reports.report_for(adapter)
    assert report is not None
    for item in report["results"]:
        assert item["status"] == "partial"  # Never reclassify an abort as success.
        assert item["sample_scope"] == "successful-key-generation"
        assert item["samples"] == expected_samples
        assert item["median_seconds"] == pytest.approx(expected_median)
        assert item["failure_count"] == 1
        assert item["outcomes"][phase]["abort"] == 1
        assert item["outcomes"][phase]["agreement_failure"] == 0
        assert item["outcomes"][phase]["error"] == 0
        assert item["outcomes"]["measured"]["agreement"] == expected_samples
    assert (directory / "pilot.json").read_bytes() == original
    assert any("exclude aborted attempts" in note for note in report["notes"])
    report["results"][0]["outcomes"][phase]["abort"] = 999
    assert reports.report_for(adapter)["results"][0]["outcomes"][phase]["abort"] == 1


@pytest.mark.parametrize("case", ["unfinished", "encapsulation-abort", "decapsulation-abort",
                                  "nonzero-exit", "all-aborted", "timeout", "disagreement", "error"])
def test_conditional_keygen_scope_does_not_hide_other_failures_or_unfinished_trials(publication, case):
    adapter, data, _, _, publish = publication
    row = data["records"][0]
    row["trials"][1].update(outcome="abort", timings_ns={})
    row["status"] = "partial"
    if case == "unfinished":
        row["trials"].pop()
    elif case in {"encapsulation-abort", "decapsulation-abort"}:
        row["trials"][1]["timings_ns"] = {"keygen": 1_000_000}
        if case == "decapsulation-abort":
            row["trials"][1]["timings_ns"]["encapsulate"] = 1_000_000
    elif case == "nonzero-exit":
        row["exit_code"] = 1
    elif case == "all-aborted":
        for trial in row["trials"]:
            if trial["phase"] == "measured":
                trial.update(outcome="abort", timings_ns={})
    elif case == "timeout":
        row["trials"][-1].update(outcome="timeout", timings_ns={})
        row.update(timed_out=True, exit_code=-9)
    else:
        row["trials"][-1]["outcome"] = "agreement_failure" if case == "disagreement" else "error"
        row.update(status="failed", exit_code=1)
    refresh(row)
    publish()
    report = reports.report_for(adapter)
    assert report is not None
    assert all(item["sample_scope"] == "partial-operations" for item in report["results"])


def test_timeout_before_first_sample_remains_visible_with_no_invented_numbers(publication):
    adapter, data, _, _, publish = publication
    row = data["records"][0]
    row.update(trials=[], status="partial", timed_out=True, exit_code=-9, setup_seconds=None)
    refresh(row)
    publish()
    result = reports.report_for(adapter)
    assert all(item["status"] == "timeout" and item["samples"] == 0 and item["median_seconds"] is None
               for item in result["results"])


@pytest.mark.parametrize("failure", ["missing", "digest", "path", "symlink", "oversize", "duplicate-index", "duplicate-json-key"])
def test_static_publication_boundary_and_missing_files_fail_closed(publication, failure):
    adapter, data, _, directory, publish = publication
    index = publish()
    target = directory / "pilot.json"
    if failure == "missing": target.unlink()
    elif failure == "digest": target.write_bytes(target.read_bytes() + b" ")
    elif failure == "path": index["reports"][0]["file"] = "../outside.json"
    elif failure == "symlink":
        outside = directory.parent / "outside.json"
        target.rename(outside)
        target.symlink_to(outside)
    elif failure == "oversize": target.write_bytes(b" " * (reports.MAX_REPORT + 1))
    elif failure == "duplicate-index": index["reports"].append(deepcopy(index["reports"][0]))
    elif failure == "duplicate-json-key":
        raw = target.read_bytes().replace(b'"format":', b'"format":"wrong","format":', 1)
        target.write_bytes(raw)
        index["reports"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
    (directory / "index.json").write_text(json.dumps(index))
    assert reports.report_for(adapter) is None


def test_cache_observes_source_and_file_replacement_without_algorithm_execution(publication):
    adapter, data, _, directory, publish = publication
    assert reports.report_for(adapter)
    data["records"][0]["notes"] = ["Updated reviewed publication"]
    publish()
    assert "Updated reviewed publication" in reports.report_for(adapter)["notes"]
    source = Path(adapter.__file__)
    source.write_text("Changed unreviewed adapter source\n")
    assert reports.report_for(adapter) is None


def test_no_publication_means_no_timing_claim(publication):
    adapter, _, _, directory, _ = publication
    (directory / "index.json").write_text(json.dumps({"format": "crucible-benchmark-publications-v1", "reports": []}))
    assert reports.report_for(adapter) is None


@pytest.fixture
def native_publication(publication):
    adapter, data, manifest, directory, publish = publication
    adapter.SCHEME_ID = manifest["scheme_id"] = "ml-kem"
    manifest["implementation"]["artifact_sha256"] = "d" * 64
    row = data["records"][0]
    row.update(scheme_id="ml-kem", artifact_sha256="d" * 64)
    row["implementation"].update(language="C", performance_level="architecture-optimized")
    files = data["harness"]["files"]
    files["scripts/reference_benchmark_native.c"] = "c" * 64
    data["harness"]["sha256"] = digest(files)
    receipt = {"format": "crucible-native-benchmark-artifact-v1", "target": "mlkem", "scheme_id": "ml-kem",
        "scheme_version": "1", "profile_id": "example-1", "parameters_sha256": row["parameters_sha256"],
        "adapter_artifact_sha256": row["artifact_sha256"], "compiler": "Synthetic clang", "build_options": ["-O1"],
        "files": {"driver/reference_benchmark_native.c": "c" * 64, "libraries/liboqs.a": "e" * 64},
        "measurement_scope": "Native operations including RNG; allocation excluded.", "binary_sha256": "f" * 64}
    receipt["sha256"] = digest(receipt)
    row["benchmark_artifact"] = receipt
    publish()
    return publication


def test_native_publication_is_bound_to_build_receipt_without_execution(native_publication):
    adapter, _, _, _, _ = native_publication
    report = reports.report_for(adapter)
    assert report["environment"]["compiler"] == "Synthetic clang"
    assert report["environment"]["runtime"].startswith("Native executable; harness")
    assert report["implementation"]["performance_level"] == "architecture-optimized"
    assert len(report["native_builds"]) == 1
    assert report["native_builds"][0]["binary_sha256"] == "f" * 64
    assert all(row["status"] == "measured" for row in report["results"])


@pytest.mark.parametrize("damage", ["digest", "driver", "parameter", "adapter", "profile", "target", "missing-library",
                                     "path", "extra-field", "bad-label", "bad-binary", "large-files"])
def test_native_publication_rejects_stale_inconsistent_or_unknown_driver(native_publication, damage):
    adapter, data, _, _, publish = native_publication
    row = data["records"][0]
    receipt = row["benchmark_artifact"]
    if damage == "driver": receipt["files"]["driver/reference_benchmark_native.c"] = "b" * 64
    elif damage == "parameter": receipt["parameters_sha256"] = "b" * 64
    elif damage == "adapter": receipt["adapter_artifact_sha256"] = "b" * 64
    elif damage == "profile": receipt["profile_id"] = "other-profile"
    elif damage == "target": receipt["target"] = "unknown"
    elif damage == "missing-library": receipt["files"].pop("libraries/liboqs.a")
    elif damage == "path": receipt["files"]["../secret.c"] = "a" * 64
    elif damage == "extra-field": receipt["execute"] = "arbitrary-program"
    elif damage == "bad-label": row["implementation"]["performance_level"] = "reference"
    elif damage == "bad-binary": receipt["binary_sha256"] = "not-a-hash"
    elif damage == "large-files": receipt["files"].update({f"source/{i}.c": "a" * 64 for i in range(513)})
    receipt["sha256"] = digest({k: v for k, v in receipt.items() if k != "sha256"})
    if damage == "digest": receipt["sha256"] = "0" * 64
    publish()
    assert reports.report_for(adapter) is None
