"""Source-equivalence checks against retained pre-port build identities.

Set NTRU_TEST_ARCHIVE to the digest-pinned upstream archive, or put it in
.native/source-cache. These checks do not compile or run a native worker.
"""
import hashlib
import importlib.util
import marshal
import os
from pathlib import Path
import shutil
import struct
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ntru_linux_candidate_builder", ROOT / "scripts/build_ntru_linux.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture
def reference_archive():
    path = Path(os.environ.get("NTRU_TEST_ARCHIVE", str(ROOT / ".native/source-cache/ntruprime-20201007.tar.gz")))
    if not path.is_file():
        pytest.skip("The explicitly provisioned pinned upstream archive is unavailable")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == builder.ARCHIVE_SHA256
    return path


@pytest.mark.parametrize("family,header_hash", [
    ("tiers", "86b9577242f73e64af7843d01862037d217852c8854c46a347dc6f47f1a4ab41"),
    ("frontier", "d01cf60b4c15573636c22097689df86c1cdf687a09c158a237010be7b005b30f"),
])
def test_generated_inputs_match_original_mac_build_bytes(tmp_path, reference_archive, family, header_hash):
    build, _, _ = builder.prepare_sources(ROOT, reference_archive, family, tmp_path / "candidate")
    # Golden digests were recorded from original Mac build directories before
    # this Linux builder existed; they are not computed using this builder.
    assert builder.sha(build / "ntru_tier_kem.c") == "40fc66dcb74cf4aedc0c096e2da6e711cc7d2d6d3da3115a9a4c3a3b5567aa16"
    assert builder.sha(build / "params.h") == "ff1e78d89e80074c86480b2667854a2eb5d320f4026b572164376ca53c9b1cce"
    assert builder.sha(build / "ntru_tier_profiles.h") == header_hash
    with pytest.raises(FileExistsError):
        builder.prepare_sources(ROOT, reference_archive, family, tmp_path / "candidate")


def test_modified_certified_parameters_fail_before_output(tmp_path, reference_archive):
    root = tmp_path / "source"
    for name in ("crucible/ntru_reference.py", "crucible/data/ntru_frontier_profiles.json",
                 "scripts/native/ntru_frontier_worker.c"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    with (root / "crucible/data/ntru_frontier_profiles.json").open("a") as output:
        output.write("\n")
    target = tmp_path / "candidate"
    with pytest.raises(ValueError, match="reviewed byte identity"):
        builder.prepare_sources(root, reference_archive, "frontier", target)
    assert not target.exists()


def test_unreviewed_archive_is_not_extracted(tmp_path):
    archive = tmp_path / "untrusted.tar.gz"
    archive.write_bytes(b"unreviewed input")
    target = tmp_path / "candidate"
    with pytest.raises(ValueError, match="reviewed byte identity"):
        builder.prepare_sources(ROOT, archive, "frontier", target)
    assert not target.exists()


@pytest.fixture
def compiler():
    name = shutil.which("cc")
    if name is None:
        pytest.skip("A C preprocessor is required for the dependency closure checks")
    return Path(name).resolve()


def test_checked_worker_and_reference_ignore_adjacent_unchecked_files(tmp_path, reference_archive, compiler):
    root = tmp_path / "source"
    for name in ("crucible/ntru_reference.py", "crucible/data/ntru_tier_profiles.json",
                 "scripts/native/ntru_tier_worker.c"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    reference = root / "crucible/ntru_reference.py"
    cached = Path(importlib.util.cache_from_source(str(reference)))
    cached.parent.mkdir()
    # A timestamp-valid cache must never substitute code for the checked .py.
    cached.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack(
        "<III", 0, int(reference.stat().st_mtime), reference.stat().st_size
    ) + marshal.dumps(compile("raise AssertionError('unchecked bytecode executed')", str(reference), "exec")))
    for name in ("ntru_tier_profiles.h", "ntru_tier_kem.c"):
        (root / "scripts/native" / name).write_text("#error Unchecked adjacent include\n")
    build, parameters, worker = builder.prepare_sources(root, reference_archive, "tiers", tmp_path / "candidate")
    assert worker.parent == build
    assert parameters.parent == build
    assert builder.sha(worker) == builder.FAMILIES["tiers"]["worker_sha256"]
    assert builder.sha(parameters) == builder.FAMILIES["tiers"]["parameters_sha256"]
    assert builder.sha(build / "ntru_tier_profiles.h") == "86b9577242f73e64af7843d01862037d217852c8854c46a347dc6f47f1a4ab41"
    (build / "randombytes.h").write_text("void randombytes(void *, unsigned long long);\n")
    # Actual preprocessing checks quoted-include resolution, not only paths.
    paths = builder.compiler_dependencies(compiler, [worker], [
        "-I" + str(build), "-I" + str(build / "subroutines"), "-DCRYPTO_NAMESPACE(x)=x"
    ])
    assert build / "ntru_tier_kem.c" in paths
    assert build / "ntru_tier_profiles.h" in paths
    assert not any(path.is_relative_to(root / "scripts/native") for path in paths)
    (root / "scripts/native/ntru_tier_worker.c").write_text("#error Changed original source\n")
    assert builder.sha(worker) == builder.FAMILIES["tiers"]["worker_sha256"]


def test_static_library_snapshot_binds_recorded_bytes(tmp_path):
    source = tmp_path / "input.a"
    snapshot = tmp_path / "snapshot.a"
    original = b"!<arch>\noriginal archive bytes"
    source.write_bytes(original)
    record = builder.snapshot_dependency(source, snapshot, 1024, static_archive=True)
    source.write_bytes(b"!<arch>\nreplacement bytes")
    assert snapshot.read_bytes() == original
    assert record == {"sha256": hashlib.sha256(original).hexdigest(), "bytes": len(original)}
    with pytest.raises(FileExistsError):
        builder.snapshot_dependency(source, snapshot, 1024, static_archive=True)


def test_installed_randombytes_header_replaces_only_reviewed_reference_shim(tmp_path, reference_archive):
    build, _, _ = builder.prepare_sources(ROOT, reference_archive, "tiers", tmp_path / "candidate")
    source = tmp_path / "installed-randombytes.h"
    source.write_text("void randombytes(void *, unsigned long long);\n")
    target = build / "randombytes.h"
    assert target.read_bytes() == b'#include "nist/rng.h"\n'
    record = builder.snapshot_dependency(source, target, 1024**2,
        replace_sha256=builder.REFERENCE_RANDOMBYTES_HEADER_SHA256)
    assert target.read_bytes() == source.read_bytes()
    assert record["sha256"] == builder.sha(target)
    assert record["replaced_sha256"] == builder.REFERENCE_RANDOMBYTES_HEADER_SHA256
    with pytest.raises(ValueError, match="reviewed byte identity"):
        builder.snapshot_dependency(source, target, 1024**2,
            replace_sha256=builder.REFERENCE_RANDOMBYTES_HEADER_SHA256)


def test_extra_static_archives_keep_explicit_order_and_distinct_snapshots(tmp_path):
    sources = []
    original_bytes = []
    for index in range(2):
        source = tmp_path / str(index) / "libsupport.a"
        source.parent.mkdir()
        data = b"!<arch>\n" + bytes([index])
        source.write_bytes(data)
        sources.append(source)
        original_bytes.append(data)
    dependencies = tmp_path / "dependencies"
    dependencies.mkdir()
    snapshots, records = builder.snapshot_extra_static_libraries(sources, dependencies)
    for source in sources:
        source.write_bytes(b"!<arch>\nchanged")
    assert len(set(snapshots)) == 2
    assert [snapshot.read_bytes() for snapshot in snapshots] == original_bytes
    assert [row["sha256"] for row in records] == [hashlib.sha256(data).hexdigest() for data in original_bytes]
    assert [row["source_path"] for row in records] == list(map(str, sources))
    assert [row["snapshot"] for row in records] == [snapshot.name for snapshot in snapshots]
    with pytest.raises(ValueError, match="At most eight"):
        builder.snapshot_extra_static_libraries(sources * 5, dependencies)


@pytest.mark.parametrize("kind", ["symlink", "oversize", "thin_archive"])
def test_dependency_snapshot_rejects_indirect_or_unbounded_inputs(tmp_path, kind):
    source = tmp_path / "input.a"
    if kind == "symlink":
        actual = tmp_path / "actual.a"
        actual.write_bytes(b"!<arch>\narchive")
        source.symlink_to(actual)
    else:
        source.write_bytes(b"!<arch>\n" + b"x" * 1024 if kind == "oversize" else b"!<thin>\nexternal members")
    output = tmp_path / "snapshot.a"
    with pytest.raises(ValueError):
        builder.snapshot_dependency(source, output, 1024, static_archive=True)
    assert not output.exists()


def openssl_fixture(tmp_path):
    primary = tmp_path / "include"
    multiarch = tmp_path / "multiarch/include"
    for path in (primary, multiarch):
        (path / "openssl").mkdir(parents=True)
    sha_header = primary / "openssl/sha.h"
    config_header = multiarch / "openssl/configuration.h"
    sha_header.write_text("#include <openssl/configuration.h>\nint SHA512(void);\n")
    config_header.write_text("#define OPENSSL_SNAPSHOT_FIXTURE 1\n")
    source = tmp_path / "hash.c"
    source.write_text("#include <openssl/sha.h>\nint fixture = OPENSSL_SNAPSHOT_FIXTURE;\n")
    return primary, multiarch, sha_header, config_header, source


def test_actual_openssl_header_closure_is_snapshotted(tmp_path, compiler):
    primary, multiarch, sha_header, config_header, source = openssl_fixture(tmp_path)
    destination = tmp_path / "snapshot/include"
    # Linux's multiarch configuration headers are system includes, searched
    # after the private -I tree even if supplied earlier in the argv.
    flags = ["-isystem", str(multiarch)]
    records = builder.snapshot_openssl_headers(compiler, [source], flags, primary, destination)
    assert {row["path"] for row in records} == {"openssl/sha.h", "openssl/configuration.h"}
    for row in records:
        copied = destination / row["path"]
        assert row["sha256"] == builder.sha(copied)
        assert row["bytes"] == copied.stat().st_size
    sha_header.write_text("#error Changed original SHA header\n")
    config_header.write_text("#error Changed original configuration header\n")
    subprocess.run([str(compiler), *flags, "-I" + str(destination), "-E", str(source)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)


def test_openssl_absolute_include_cannot_escape_snapshot(tmp_path, compiler):
    primary, multiarch, sha_header, config_header, source = openssl_fixture(tmp_path)
    sha_header.write_text(f'#include "{config_header}"\nint SHA512(void);\n')
    with pytest.raises(ValueError, match="unsnapshotted OpenSSL"):
        builder.snapshot_openssl_headers(compiler, [source], [], primary, tmp_path / "snapshot/include")
