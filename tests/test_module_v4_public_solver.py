import importlib.util
import json
from pathlib import Path

import pytest

from crucible import lattice_module_v4 as module
from crucible import module_compressed_v4_core as core

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("module_v4_public_solver", ROOT / "scripts/lattice_solve_module_v4_tier1.py")
solver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(solver)


def prepare(tmp_path):
    challenge = module.generate(solver.PROFILE_ID)
    package = {"challenge":{"scheme_id":module.SCHEME_ID,"scheme_version":module.SCHEME_VERSION,"public":challenge.public},
               "scheme_manifest":module.get_manifest(solver.PROFILE_ID)}
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference/protocol.py").write_bytes(module.source_bytes())
    (tmp_path / "reference/implementation-artifacts.json").write_text(json.dumps(module.artifact_manifest()))
    for name in module._FILES:
        target = tmp_path / "reference/source" / name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    return challenge, package


def test_solver_uses_only_public_deterministic_encryption(monkeypatch):
    challenge = module.generate(solver.PROFILE_ID)
    def forbidden(*args,**kwargs):
        pytest.fail("Public solver accessed a private operation or fresh randomness")
    for owner,name in ((module,"generate"),(module,"keygen"),(module,"decapsulate"),
                       (module,"encapsulate"),(module.os,"urandom"),
                       (core,"keygen_internal"),(core,"decapsulate"),(core,"_decrypt")):
        monkeypatch.setattr(owner,name,forbidden)
    assert solver.solve(challenge.public,core) == challenge.target


def test_downloaded_workspace_full_closure_executes_only_public_core(tmp_path):
    challenge, package = prepare(tmp_path)
    downloaded = solver._workspace_core(package,tmp_path)
    # This module contains no adapter generation/OS randomness imports.
    assert not hasattr(downloaded,"generate") and not hasattr(downloaded,"os")
    assert solver.solve(challenge.public,downloaded) == challenge.target


@pytest.mark.parametrize("name", ["reference/protocol.py","reference/source/crucible/module_compressed_v4_core.py",
                                "reference/source/crucible/lattice_module_v4.py"])
def test_any_source_tampering_rejected_before_execution(tmp_path,name):
    _, package = prepare(tmp_path)
    (tmp_path / name).write_text("raise RuntimeError('this must never execute')")
    with pytest.raises(ValueError):
        solver._workspace_core(package,tmp_path)


def test_symlinked_reference_ancestor_is_rejected(tmp_path):
    _, package = prepare(tmp_path)
    original = tmp_path / "reference/source/crucible"
    renamed = tmp_path / "other"
    original.rename(renamed)
    original.symlink_to(renamed,target_is_directory=True)
    with pytest.raises(ValueError,match="symbolic"):
        solver._workspace_core(package,tmp_path)
