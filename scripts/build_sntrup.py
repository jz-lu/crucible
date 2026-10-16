#!/usr/bin/env python3
"""Explicit offline-capable builder, never called by the server.

Downloads require --download; every archive is checked before unpacking or
execution. A new executable digest must be reviewed/pinned in sntrup.py before
it can run. The original 2020 reference is optional and test-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
SOURCES = (
    ("libcpucycles-20260901", "https://cpucycles.cr.yp.to/libcpucycles-20260901.tar.gz", "c914c7275952ed00bb188e1170409c83b61abfd0b828cdb2b5a87275be43344e"),
    ("librandombytes-20240318", "https://randombytes.cr.yp.to/librandombytes-20240318.tar.gz", "fae6fb839096e54ce8abb6dc8ae46ed67b02034474e83cbda088eddd2e584641"),
    ("libntruprime-20260717", "https://libntruprime.cr.yp.to/libntruprime-20260717.tar.gz", "768350cd57a9395d80545d2091ed27cd7ccf93c09b41671e4e50e053bcb0bff1"),
)
REFERENCE = ("ntruprime-20201007", "https://ntruprime.cr.yp.to/nist/ntruprime-20201007.tar.gz", "280e5dad090144446a49cc0e283163b8167a7961f5ccb183817501fcc3b695ce")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive(cache, item, download):
    name, url, expected = item
    path = cache / (name + ".tar.gz")
    if not path.exists():
        if not download:
            raise SystemExit(f"Missing {path.name}; supply source cache or use --download")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Crucible source builder"}), timeout=60) as response:
            data = response.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
            raise SystemExit(f"Source archive digest/size mismatch: {name}")
        path.write_bytes(data)
    if path.stat().st_size > 32 * 1024 * 1024 or digest(path) != expected:
        raise SystemExit(f"Source archive digest/size mismatch: {name}")
    return path


def run(args, cwd, env, log):
    # Build-time subprocesses have bounded completion; no server/user payloads.
    subprocess.run(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-cache", type=Path, default=ROOT / ".native/source-cache")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--reference", action="store_true", help="Also build the old reference for differential tests")
    parser.add_argument("--openssl-prefix", type=Path, default=Path("/opt/homebrew/opt/openssl@3"))
    parser.add_argument("--jobs", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    prefix = ROOT / ".native/sntrup"
    source = prefix / "source"
    for path in (args.source_cache, prefix, source):
        path.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    include_paths, library_paths = [str(prefix / "include")], [str(prefix / "lib")]
    if args.openssl_prefix.exists():
        include_paths.append(str(args.openssl_prefix / "include"))
        library_paths.append(str(args.openssl_prefix / "lib"))
    env.update(CPATH=os.pathsep.join(include_paths), LIBRARY_PATH=os.pathsep.join(library_paths),
               LD_LIBRARY_PATH=str(prefix / "lib"), DYLD_LIBRARY_PATH=str(prefix / "lib"))
    items = SOURCES + ((REFERENCE,) if args.reference else ())
    for item in items:
        path = archive(args.source_cache, item, args.download)
        with tarfile.open(path) as tar:
            tar.extractall(source, filter="data")
    with (prefix / "build.log").open("w") as log:
        for name, _, _ in SOURCES:
            options = ["--prefix=" + str(prefix)]
            if name.startswith("libntruprime"):
                options.append("--no-trim")
                if platform.system() == "Darwin":
                    options.append("--no-valgrind")
            print("Building", name, flush=True)
            run([sys.executable, "configure", *options], source / name, env, log)
            run(["make", f"-j{args.jobs}", "install"], source / name, env, log)
        worker = prefix / "crucible-sntrup-worker"
        run(["cc", "-O2", "-std=c99", "-Wall", "-Wextra", "-I", str(prefix / "include"),
             "scripts/native/sntrup_worker.c", str(prefix / "lib/libntruprime.a"),
             str(prefix / "lib/librandombytes-kernel.a"), "-o", str(worker)], ROOT, env, log)
        print("Testing the published sntrup761 profile", flush=True)
        run([str(prefix / "bin/ntruprime-test"), "kem", "sntrup761"], ROOT, env, log)
        if args.reference:
            ref = source / "ntruprime-20201007/Reference_Implementation/kem/sntrup761"
            files = ["nist/rng.c", "Decode.c", "Encode.c", "int32.c", "kem.c", "uint32.c",
                     "subroutines/crypto_hash_sha512.c", "subroutines/crypto_sort_uint32.c"]
            run(["cc", "-shared", "-fPIC", "-O2", "-I.", "-Isubroutines", "-DCRYPTO_NAMESPACE(x)=x",
                 *files, "-lcrypto", "-o", str(prefix / "reference-20201007.dylib")], ref, env, log)
    record = {"format": "crucible-native-build-v1", "platform": platform.system() + " " + platform.machine(),
              "sources": [{"name": n, "url": u, "sha256": d} for n, u, d in items],
              "build_script_sha256": digest(Path(__file__)),
              "worker_source_sha256": digest(ROOT / "scripts/native/sntrup_worker.c"),
              "worker_sha256": digest(worker),
              "static_library_sha256": digest(prefix / "lib/libntruprime.a"),
              "randombytes_kernel_sha256": digest(prefix / "lib/librandombytes-kernel.a"),
              "compiler": subprocess.check_output(["cc", "--version"], text=True).splitlines()[0],
              "upstream_conventional_sntrup761_tests": "passed",
              "full_upstream_timecop_suite": "not executed by this builder"}
    (prefix / "build-manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))
    print("Review the artifact and pin its digest in crucible/sntrup.py before use; no automatic registration or admission.")


if __name__ == "__main__":
    main()
