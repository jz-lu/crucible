"""A distinct HQC timing target retains exact old/new source and receipt pins."""
from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest

from crucible import benchmark_reports as reports
from scripts import reference_benchmark_adapters as targets
from scripts import reference_benchmark_native as native
from scripts.reference_benchmark_adapters import digest
from test_reference_benchmark_native import native_builder
from test_benchmark_reports import native_publication, publication


def test_new_hqc_target_does_not_retarget_historical_hqc():
    assert targets.TARGETS["hqc"][0] == "crucible.code_hqc_v2"
    assert targets.TARGETS["hqc-v3"][0] == "crucible.code_hqc_v3"
    assert {"hqc", "hqc-v3"} <= targets.NATIVE_TARGETS
    assert targets.implementation("hqc-v3")["performance_level"] == "reference"


@pytest.mark.parametrize("target", ["hqc", "hqc-v3"])
def test_hqc_source_tree_is_selected_and_verified_for_its_exact_release(tmp_path, target):
    config = replace(native.BuildInputs(), hqc_root=tmp_path / "old", hqc_model_root=tmp_path / "new")
    contents = {"hqc": b"old pinned source", "hqc-v3": b"new pinned source"}
    for name, root in (("hqc", config.hqc_root), ("hqc-v3", config.hqc_model_root)):
        folder = root / "source/hqc-1"
        folder.mkdir(parents=True)
        (folder / "api.h").write_bytes(contents[name])
    prefix = f"liboqs-{native.OQS_COMMIT}/src/kem/hqc/pqc-hqc_hqc-1_ref/"
    upstream = {prefix + "api.h": hashlib.sha256(b"upstream").hexdigest()}
    closure = {"workers": {"hqc-1": {"patched_files": {
        "api.h": hashlib.sha256(contents[target]).hexdigest()}}}, "base_static_library_sha256": native.OQS_LIBRARY}
    inputs = {}
    native._code_inputs(target, {"instance_size": 1}, config, inputs, upstream, closure)
    assert inputs == {"source/api.h": contents[target]}
    swapped = replace(config, hqc_root=config.hqc_model_root, hqc_model_root=config.hqc_root)
    with pytest.raises(ValueError):
        native._code_inputs(target, {"instance_size": 1}, swapped, {}, upstream, closure)


def test_new_driver_keeps_hqc_family_and_exact_adapter_binding(native_builder, monkeypatch):
    _, manifest, config, commands = native_builder
    closure = {"artifact_sha256": "a" * 64}
    module = SimpleNamespace(SCHEME_ID="hqc", SCHEME_VERSION="modeled-research-v3", verify_artifact=lambda: closure)
    monkeypatch.setattr(native, "adapter", lambda target: module if target == "hqc-v3" else None)
    manifest.update(scheme_id="hqc", version=module.SCHEME_VERSION,
                    parameters={"n": 17669, "secret_weight": 66}, instance_size=66)
    def sources(target, row, config, inputs, hashes, actual_closure):
        assert target == "hqc-v3" and actual_closure == closure and row["instance_size"] == 66
        inputs.update({"source/api.h": b"known header", "source/kem.c": b"known core"})
    monkeypatch.setattr(native, "_code_inputs", sources)
    _, selector, receipt = native.prepare("hqc-v3", manifest, config)
    assert selector == "fixed" and receipt["target"] == "hqc-v3"
    assert receipt["scheme_version"] == "modeled-research-v3"
    assert "-DBENCH_FAMILY=1" in commands[0] and "-DUSE_OQS_RANDOMBYTES" in commands[0]
    assert receipt["parameters_sha256"] == digest(manifest["parameters"])
    assert receipt["files"]["source/kem.c"] == hashlib.sha256(b"known core").hexdigest()


@pytest.mark.parametrize("version,accepted", [("modeled-research-v3", True), ("growth-research-v2", False)])
def test_hqc_v3_report_cannot_be_relabelled_as_the_old_release(native_publication, version, accepted):
    adapter, data, manifest, _, publish = native_publication
    adapter.SCHEME_ID = manifest["scheme_id"] = "hqc"
    adapter.SCHEME_VERSION = manifest["version"] = version
    row = data["records"][0]
    row.update(scheme_id="hqc", version=version)
    row["implementation"].update(label="Synthetic HQC reference", performance_level="reference")
    receipt = row["benchmark_artifact"]
    receipt.update(target="hqc-v3", scheme_id="hqc", scheme_version=version)
    receipt["sha256"] = digest({key: value for key, value in receipt.items() if key != "sha256"})
    publish()
    result = reports.report_for(adapter)
    assert (result is not None) == accepted
