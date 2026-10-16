#!/usr/bin/env python3
"""Explicit offline builder for certified Streamlined NTRU Prime frontier research tiers.

Does not download, activate schemes, edit a registry, or accept user source.
Requires the already pinned 2020 source archive and static dependency archives.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from crucible.ntru_reference import encoded_length

ARCHIVE_SHA256 = "280e5dad090144446a49cc0e283163b8167a7961f5ccb183817501fcc3b695ce"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Pinned reference substitution no longer matches exactly once")
    return text.replace(old, new)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-cache", type=Path, default=ROOT / ".native/source-cache")
    parser.add_argument("--base-native", type=Path, default=ROOT / ".native")
    parser.add_argument("--openssl-prefix", type=Path, default=Path("/opt/homebrew/opt/openssl@3"))
    args = parser.parse_args()
    archive = args.source_cache / "ntruprime-20201007.tar.gz"
    if sha(archive) != ARCHIVE_SHA256:
        raise SystemExit("Original reference archive differs from reviewed digest")
    out = ROOT / ".native/ntru-frontier"
    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(out / "source", filter="data")
    ref = out / "source/ntruprime-20201007/Reference_Implementation/kem/sntrup761"
    build = out / "build"
    if build.exists():
        shutil.rmtree(build)
    shutil.copytree(ref, build)
    data_path = ROOT / "crucible/data/ntru_frontier_profiles.json"
    data = json.loads(data_path.read_text())
    profiles = data["profiles"]
    if not 1 <= len(profiles) <= 100 or [r["tier"] for r in profiles] != list(range(1, len(profiles)+1)):
        raise SystemExit("Expected consecutively numbered already-certified profiles")
    table = []
    for row in profiles:
        p, q, w = (row[k] for k in ("p", "q", "w"))
        table.append([p, q, w, encoded_length(p, q), encoded_length(p, (q + 2) // 3)])
    (build / "ntru_tier_profiles.h").write_text(f"#define PROFILE_COUNT {len(profiles)}\nstatic const int profiles[PROFILE_COUNT][5] = {{\n" +
        ",\n".join("{" + ",".join(map(str, r)) + "}" for r in table) + "\n};\n")
    (build / "params.h").write_text("#define p active_p\n#define q active_q\n#define w active_w\n#define Rq_bytes active_pk\n#define Rounded_bytes active_rounded\n")
    text = (ref / "kem.c").read_text()
    # Exact full-support uniform samplers replace the upstream near-uniform
    # fixed-32-bit maps. No seed truncation or restricted-support shortcuts.
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
    # Production calls the static specification functions directly. Do not
    # retain the hard-coded sntrup761 external API macros at the bottom.
    text = text.split("/* ----- crypto_kem API */")[0]
    (build / "ntru_tier_kem.c").write_text(text)
    shutil.copyfile(args.base_native / "sntrup/include/randombytes.h", build / "randombytes.h")
    random_library = args.base_native / "sntrup/lib/librandombytes-kernel.a"
    crypto_library = args.openssl_prefix / "lib/libcrypto.a"
    if sha(random_library) != "ce788c274450011db177c035404f3a268e3c2357c2859fe5bc331be1c3b75f88":
        raise SystemExit("Static OS randomness dependency differs from its reviewed pin")
    if sha(crypto_library) != "4b3f32521ed371b110cdea53670aa2561163163e20cc71ddbaf6530916bf868f":
        raise SystemExit("Static OpenSSL dependency differs from its reviewed pin")
    worker = out / "crucible-ntru-frontier-worker"
    sources = [ROOT / "scripts/native/ntru_frontier_worker.c", build / "Decode.c", build / "Encode.c",
               build / "int32.c", build / "uint32.c", build / "subroutines/crypto_hash_sha512.c",
               build / "subroutines/crypto_sort_uint32.c"]
    cmd = ["cc", "-O2", "-std=c99", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-unused-parameter",
           "-I" + str(build), "-I" + str(build / "subroutines"), "-I" + str(args.openssl_prefix / "include"),
           "-DCRYPTO_NAMESPACE(x)=x", *map(str, sources), str(random_library), str(crypto_library), "-o", str(worker)]
    subprocess.run(cmd, check=True, timeout=120)
    record = {"format": "crucible-ntru-frontier-build-v1", "upstream_archive_sha256": ARCHIVE_SHA256,
              "parameters_file_sha256": sha(data_path), "build_script_sha256": sha(Path(__file__)),
              "worker_source_sha256": sha(ROOT / "scripts/native/ntru_frontier_worker.c"),
              "adapted_core_sha256": sha(build / "ntru_tier_kem.c"), "worker_sha256": sha(worker),
              "randombytes_static_sha256": sha(random_library), "libcrypto_static_sha256": sha(crypto_library),
              "compiler": subprocess.check_output(["cc", "--version"], text=True).splitlines()[0],
              "external_runtime": "System libc/libSystem only; cryptographic and OS-randomness libraries statically linked",
              "sampler": "Exact rejection-based uniform coefficients; partial Fisher-Yates uniform support and independent signs"}
    (out / "build-manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))
    print("Review and pin this worker in ntru_frontier.py before runtime use. No activation performed.")


if __name__ == "__main__":
    main()
