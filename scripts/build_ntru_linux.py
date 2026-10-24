#!/usr/bin/env python3
"""Build Linux NTRU candidates without changing the original Mac builders.

Only the already reviewed 2020 reference, parameter tables and worker sources
are accepted. The adapted core must have exactly the historical byte hash.
Dependency and toolchain hashes are recorded observations, not runtime approval.
Run explicitly in an isolated Linux build checkout; no download or admission.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SHA256 = "280e5dad090144446a49cc0e283163b8167a7961f5ccb183817501fcc3b695ce"
CORE_SHA256 = "40fc66dcb74cf4aedc0c096e2da6e711cc7d2d6d3da3115a9a4c3a3b5567aa16"
REFERENCE_SHA256 = "a18478c044a538221d6255f6211487e39ed872a8eeb713df44ca6d612b41d0ed"
REFERENCE_RANDOMBYTES_HEADER_SHA256 = hashlib.sha256(b'#include "nist/rng.h"\n').hexdigest()
FAMILIES = {
    "tiers": {"version": "2", "directory": "ntru-tiers", "stem": "ntru_tier",
              "parameters_sha256": "a9873c40310e54803fb3ba939d3a5d4508e08e0884863d8bb3bf248b88c74e46",
              "worker_sha256": "948e4440698271c20d0183328bbe26622b0a1ef7bf47ee74e7b3b4f4c3235f5e"},
    "frontier": {"version": "3", "directory": "ntru-frontier", "stem": "ntru_frontier",
                 "parameters_sha256": "5ce65e7785a6dee839c8a46de39153873c54fbbd426c4e1f1df558f68f8f0767",
                 "worker_sha256": "694142f2b2c227fee7563976337a49a6c8f13e39905964b733994e66e75f7d0b"},
}


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def bounded_bytes(path, maximum):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError("Expected bounded regular dependency file")
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if not 0 < len(data) <= maximum:
        raise ValueError("Expected bounded regular dependency file")
    return data


def byte_identity(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def checked_file(path, expected, maximum):
    try:
        data = bounded_bytes(path, maximum)
    except ValueError as error:
        raise ValueError("Source differs from its reviewed byte identity") from error
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Source differs from its reviewed byte identity")
    return data


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Historical reference substitution no longer matches exactly once")
    return text.replace(old, new)


def adapt_core(text):
    # These are precisely the original builders' transformations. Final byte
    # equality is required, so portability cannot silently alter the sampler.
    text = replace_once(text, "typedef small Inputs[p];", "typedef small *Inputs;")
    text = text.replace("  Inputs r;", "  small r[p];")
    old = """static void Short_random(small *out)
{
  uint32 L[p];
  int i;

  for (i = 0;i < p;++i) L[i] = urandom32();
  Short_fromlist(out,L);
}"""
    new = """static uint32 Uniform_below(uint32 bound)
{
  uint32 x, threshold = (uint32)(-bound) % bound;
  do { x = urandom32(); } while (x < threshold);
  return x % bound;
}

static void Short_random(small *out)
{
  int positions[p], i, j, t;
  for (i = 0; i < p; ++i) { positions[i] = i; out[i] = 0; }
  for (i = 0; i < w; ++i) {
    j = i + Uniform_below(p-i);
    t = positions[i]; positions[i] = positions[j]; positions[j] = t;
    out[positions[i]] = Uniform_below(2) ? 1 : -1;
  }
}"""
    text = replace_once(text, old, new)
    text = replace_once(text, "(((urandom32()&0x3fffffff)*3)>>30)-1", "(int)Uniform_below(3)-1")
    marker = "/* ----- crypto_kem API */"
    if text.count(marker) != 1:
        raise ValueError("Historical API boundary changed")
    text = text.split(marker)[0]
    if hashlib.sha256(text.encode()).hexdigest() != CORE_SHA256:
        raise ValueError("Adapted reference differs from the historical core")
    return text


def prepare_sources(root, archive, family, output):
    spec = FAMILIES[family]
    parameters = root / "crucible/data" / (spec["stem"] + "_profiles.json")
    worker_source = root / "scripts/native" / (spec["stem"] + "_worker.c")
    reference = root / "crucible/ntru_reference.py"
    archive_bytes = checked_file(archive, ARCHIVE_SHA256, 32 * 1024**2)
    parameter_bytes = checked_file(parameters, spec["parameters_sha256"], 2 * 1024**2)
    worker_bytes = checked_file(worker_source, spec["worker_sha256"], 1024**2)
    reference_bytes = checked_file(reference, REFERENCE_SHA256, 1024**2)
    # Execute exactly the verified source bytes, never timestamp-valid cached
    # bytecode or a second read of a mutable source file.
    reference_namespace = {"__name__": "crucible_ntru_encoding"}
    exec(compile(reference_bytes, str(reference), "exec"), reference_namespace)
    encoded_length = reference_namespace["encoded_length"]
    profiles = json.loads(parameter_bytes)["profiles"]
    if (not 1 <= len(profiles) <= 100 or (family == "tiers" and len(profiles) != 50)
            or [row["tier"] for row in profiles] != list(range(1, len(profiles) + 1))):
        raise ValueError("Certified profile inventory changed")
    output.mkdir(parents=True, exist_ok=False)  # Never replace a prior candidate.
    with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as source:
        source.extractall(output / "source", filter="data")
    ref = output / "source/ntruprime-20201007/Reference_Implementation/kem/sntrup761"
    build = output / "build"
    shutil.copytree(ref, build)
    # Quoted includes search the worker's own directory first. Keeping the
    # checked worker beside the generated files prevents unchecked adjacent
    # files in scripts/native from shadowing these verified inputs.
    worker_snapshot = build / worker_source.name
    worker_snapshot.write_bytes(worker_bytes)
    parameter_snapshot = build / parameters.name
    parameter_snapshot.write_bytes(parameter_bytes)
    table = []
    for row in profiles:
        p, q, w = (row[key] for key in ("p", "q", "w"))
        table.append([p, q, w, encoded_length(p, q), encoded_length(p, (q + 2) // 3)])
    prefix = ("static const int profiles[50][5] = {\n" if family == "tiers" else
              f"#define PROFILE_COUNT {len(profiles)}\nstatic const int profiles[PROFILE_COUNT][5] = {{\n")
    (build / "ntru_tier_profiles.h").write_text(prefix +
        ",\n".join("{" + ",".join(map(str, row)) + "}" for row in table) + "\n};\n")
    (build / "params.h").write_text("#define p active_p\n#define q active_q\n#define w active_w\n#define Rq_bytes active_pk\n#define Rounded_bytes active_rounded\n")
    (build / "ntru_tier_kem.c").write_text(adapt_core((ref / "kem.c").read_text()))
    return build, parameter_snapshot, worker_snapshot


def snapshot_dependency(source, destination, maximum, *, static_archive=False, replace_sha256=None):
    data = bounded_bytes(source, maximum)
    if static_archive and not data.startswith(b"!<arch>\n"):
        raise ValueError("Static archive inputs are required")
    if replace_sha256 is not None:
        # The original Mac builders replace the archive's NIST RNG shim with
        # the installed library header. Permit only that known input, never an
        # arbitrary existing snapshot or a symlink.
        checked_file(destination, replace_sha256, 1024**2)
        destination.unlink()
    with destination.open("xb") as target:
        target.write(data)
    record = byte_identity(data)
    if replace_sha256 is not None:
        record["replaced_sha256"] = replace_sha256
    return record


def snapshot_extra_static_libraries(sources, dependency_dir):
    if len(sources) > 8:
        raise ValueError("At most eight explicit extra static libraries are supported")
    snapshots, records = [], []
    for index, source in enumerate(sources):
        target = dependency_dir / f"extra-{index}-{source.name}"
        record = snapshot_dependency(source, target, 64 * 1024**2, static_archive=True)
        snapshots.append(target)
        records.append({"source_path": str(source.absolute()), "snapshot": target.name, **record})
    return snapshots, records


def compiler_dependencies(compiler, sources, flags):
    result = subprocess.run([str(compiler), *flags, "-M", "-MT", "crucible_headers", *map(str, sources)],
                            check=True, capture_output=True, text=True, timeout=60)
    if len(result.stdout) > 4 * 1024**2:
        raise ValueError("Unexpected compiler dependency inventory")
    paths = set()
    for line in result.stdout.replace("\\\n", "").splitlines():
        if not line.startswith("crucible_headers:") or "$" in line:
            raise ValueError("Unsupported compiler dependency inventory")
        paths.update(Path(name) for name in shlex.split(line.split(":", 1)[1]))
    if not paths or len(paths) > 4096:
        raise ValueError("Unexpected compiler dependency inventory")
    return paths


def openssl_relative(path):
    # OpenSSL configuration headers may come from a separate multiarch include
    # root. Preserve their openssl/ suffix in one private include tree.
    parts = path.parts
    if "openssl" not in parts:
        return None
    index = len(parts) - 1 - parts[::-1].index("openssl")
    relative = Path(*parts[index:])
    if ".." in relative.parts or relative.suffix != ".h":
        raise ValueError("Unexpected OpenSSL header path")
    return relative


def snapshot_openssl_headers(compiler, sources, flags, source_include, destination):
    observed = compiler_dependencies(compiler, sources, [*flags, "-I" + str(source_include)])
    records = {}
    total_bytes = 0
    for source in sorted(observed):
        relative = openssl_relative(source)
        if relative is None:
            continue
        data = bounded_bytes(source.resolve(strict=True), 1024**2)
        identity = byte_identity(data)
        key = relative.as_posix()
        if key in records:
            if records[key]["sha256"] != identity["sha256"]:
                raise ValueError("Conflicting OpenSSL header search roots")
            continue
        total_bytes += len(data)
        if len(records) >= 256 or total_bytes > 16 * 1024**2:
            raise ValueError("Unexpected OpenSSL header inventory")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(data)
        records[key] = {"path": key, "source_path": str(source), **identity}
    if "openssl/sha.h" not in records:
        raise ValueError("Compiler did not resolve the expected OpenSSL SHA header")
    # Check the actual compile search path again: no OpenSSL header may fall
    # back to an unsnapshotted system or environment include directory.
    resolved = compiler_dependencies(compiler, sources, [*flags, "-I" + str(destination)])
    used = set()
    for path in resolved:
        relative = openssl_relative(path)
        if relative is None:
            continue
        key = relative.as_posix()
        if (key not in records or path.resolve() != (destination / relative).resolve()
                or sha(path) != records[key]["sha256"]):
            raise ValueError("Compiler resolved an unsnapshotted OpenSSL header")
        used.add(key)
    if used != records.keys():
        raise ValueError("OpenSSL header closure changed after snapshot")
    return [records[key] for key in sorted(records)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=tuple(FAMILIES), required=True)
    parser.add_argument("--source-cache", type=Path, required=True)
    parser.add_argument("--randombytes-prefix", type=Path, required=True)
    parser.add_argument("--crypto-library", type=Path, required=True,
                        help="Exact Linux static libcrypto.a; no guessed Homebrew/multiarch path")
    parser.add_argument("--extra-static-library", type=Path, action="append", default=[],
                        help="Explicit dependency archive, repeat in link order after libcrypto (for example libz.a, libzstd.a)")
    parser.add_argument("--openssl-include", type=Path, default=Path("/usr/include"))
    parser.add_argument("--output", type=Path, required=True, help="New output directory inside this isolated checkout")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("Run in the isolated Linux AMD64 build environment")
    marker = ROOT / ".crucible-linux-build-worktree"
    if marker.is_symlink() or not marker.is_file() or marker.read_text().strip() != "crucible-isolated-linux-build-v1":
        parser.error("An explicit isolated build checkout is required")
    if any(name.startswith("CRUCIBLE_") or name in {"DATABASE_URL", "PGPASSWORD", "PGPASSFILE", "PGSERVICE", "PGSERVICEFILE"} for name in os.environ):
        parser.error("Build without service or database environment variables")
    output = args.output.absolute()
    if (output.exists() or not output.resolve().is_relative_to(ROOT.resolve())
            or any(parent.is_symlink() for parent in (output, *output.parents))):
        parser.error("Use a new ordinary output directory inside the isolated checkout")
    random_library = args.randombytes_prefix / "lib/librandombytes-kernel.a"
    random_header = args.randombytes_prefix / "include/randombytes.h"
    compiler_name = shutil.which("cc")
    if compiler_name is None:
        parser.error("A C compiler is required")
    compiler = Path(compiler_name).resolve(strict=True)
    compiler_identity = byte_identity(bounded_bytes(compiler, 256 * 1024**2))
    build, parameters, worker_source = prepare_sources(
        ROOT, args.source_cache / "ntruprime-20201007.tar.gz", args.family, output)
    dependency_dir = build / "dependencies"
    dependency_dir.mkdir()
    random_snapshot = dependency_dir / "librandombytes-kernel.a"
    crypto_snapshot = dependency_dir / "libcrypto.a"
    dependencies = {
        "randombytes_static": snapshot_dependency(random_library, random_snapshot, 16 * 1024**2, static_archive=True),
        "randombytes_header": snapshot_dependency(random_header, build / "randombytes.h", 1024**2,
            replace_sha256=REFERENCE_RANDOMBYTES_HEADER_SHA256),
        "libcrypto_static": snapshot_dependency(args.crypto_library, crypto_snapshot, 64 * 1024**2, static_archive=True),
    }
    extra_libraries, dependencies["extra_static_libraries"] = snapshot_extra_static_libraries(
        args.extra_static_library, dependency_dir)
    spec = FAMILIES[args.family]
    worker = output / ("crucible-ntru-tier-worker" if args.family == "tiers" else "crucible-ntru-frontier-worker")
    sources = [worker_source, *[build / name for name in
        ("Decode.c", "Encode.c", "int32.c", "uint32.c", "subroutines/crypto_hash_sha512.c", "subroutines/crypto_sort_uint32.c")]]
    flags = ["-O2", "-std=c99", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-unused-parameter"]
    compile_flags = [*flags, "-I" + str(build), "-I" + str(build / "subroutines"),
                     "-DCRYPTO_NAMESPACE(x)=x", "-pthread"]
    openssl_include = dependency_dir / "openssl/include"
    dependencies["openssl_headers"] = snapshot_openssl_headers(
        compiler, sources, compile_flags, args.openssl_include, openssl_include)
    command = [str(compiler), *compile_flags, "-I" + str(openssl_include), *map(str, sources),
               str(random_snapshot), str(crypto_snapshot), *map(str, extra_libraries), "-ldl", "-o", str(worker)]
    # Explicit trusted sources, argv execution, bounded compiler, no user payload.
    subprocess.run(command, check=True, timeout=300)
    if sha(compiler) != compiler_identity["sha256"]:
        raise ValueError("Compiler binary changed during the build")
    record = {"format": "crucible-ntru-linux-candidate-v1", "scheme_id": "sntrup", "version": spec["version"],
              "platform": {"system": platform.system(), "machine": platform.machine()},
              "upstream_archive_sha256": ARCHIVE_SHA256, "parameters_file_sha256": sha(parameters),
              "build_script_sha256": sha(Path(__file__)), "worker_source_sha256": sha(worker_source),
              "adapted_core_sha256": sha(build / "ntru_tier_kem.c"),
              "profile_header_sha256": sha(build / "ntru_tier_profiles.h"),
              "worker_sha256": sha(worker), "dependencies": dependencies,
              "compiler": subprocess.check_output([str(compiler), "--version"], text=True, timeout=10).splitlines()[0],
              "compiler_binary": {"path": str(compiler), **compiler_identity},
              "compiler_flags": flags, "extra_link_flags": ["-pthread", "-ldl"],
              "elf_dynamic_dependencies": subprocess.check_output(["readelf", "-d", str(worker)], text=True, timeout=10),
              "admitted": False, "conformance_tested": False,
              "scope": "Observed candidate build only; independent byte/conformance and runtime review required.",
              "dependency_scope": "Checked source bytes, static libraries and resolved OpenSSL headers are local snapshots. "
                  "The compiler driver is hashed, not copied. Other system headers, compiler subprograms, linker and runtime "
                  "are not pinned; this is not a hermetic or reproducible-toolchain claim."}
    (output / "build-manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"candidate": args.family, "worker_sha256": sha(worker), "admitted": False}))


if __name__ == "__main__":
    main()
