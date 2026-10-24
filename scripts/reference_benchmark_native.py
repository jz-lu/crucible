"""Offline, explicit builds of timing drivers over pinned native implementations.

Input trees are read only. Outputs must live beneath this checkout's .local;
no challenge worker, application data or imported adapter is modified.
"""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

from scripts.reference_benchmark_adapters import ROOT, adapter, digest, file_digest

OQS_COMMIT = "5a1a854b0dc9f2141bdc771c555ee60c37950183"
OQS_ARCHIVE = "83c6e6cff490638312e1e20ed5de593fb5d7bfab162235f6238f519d6a1feafb"
OQS_LIBRARY = "7b550ba37b718917e4d7bbc973dfd47a24d0a6a4e75b8205e6161d21b199718c"
OQS_CONFIG = "2cf61441933a85d2162fe3e6fb104b262a95cba2621ee8681d4f7034687221d1"
NTRU_ARCHIVE = "280e5dad090144446a49cc0e283163b8167a7961f5ccb183817501fcc3b695ce"
NTRU_CORE = "40fc66dcb74cf4aedc0c096e2da6e711cc7d2d6d3da3115a9a4c3a3b5567aa16"
RANDOM_LIBRARY = "ce788c274450011db177c035404f3a268e3c2357c2859fe5bc331be1c3b75f88"
RANDOM_HEADER = "a5f63f1721fce1768a30ddc52d8950818731b77a2adedc92cd35e75e0a5b14df"
CRYPTO_LIBRARY = "4b3f32521ed371b110cdea53670aa2561163163e20cc71ddbaf6530916bf868f"
TARGETS = {"hqc": 1, "hqc-v3": 1, "mceliece": 2, "ntru": 3, "mlkem": 0}
HQC_TARGETS = frozenset({"hqc", "hqc-v3"})


@dataclass(frozen=True)
class BuildInputs:
    pq_root: Path = ROOT / ".native-pq"
    code_root: Path = ROOT / ".native-code"
    hqc_root: Path = ROOT / ".native-hqc-growth"
    hqc_model_root: Path = ROOT / ".local/native-hqc-model"
    ntru_root: Path = ROOT / ".native/ntru-frontier"
    ntru_archive: Path = ROOT / ".native/source-cache/ntruprime-20201007.tar.gz"
    random_library: Path = ROOT / ".native/sntrup/lib/librandombytes-kernel.a"
    openssl_prefix: Path = Path("/opt/homebrew/opt/openssl@3")
    output: Path = ROOT / ".local/reference-native"


def require(value, message="Pinned native benchmark input is missing or inconsistent"):
    if not value:
        raise ValueError(message)


def checked(path, expected=None, limit=16 * 1024 * 1024):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= limit)
    raw = path.read_bytes()
    require(len(raw) <= limit and (expected is None or hashlib.sha256(raw).hexdigest() == expected))
    return raw


@lru_cache(maxsize=4)
def _archive(path, expected, signature):
    # Never extract a supplied archive or use its paths as output paths.
    checked(path, expected)
    result = {}
    with tarfile.open(path) as archive:
        for member in archive:
            if member.isfile():
                require(member.size <= 16 * 1024 * 1024 and member.name not in result)
                result[member.name] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    require(Path(path).stat().st_mtime_ns == signature[0] and Path(path).stat().st_size == signature[1])
    return result


def archive_hashes(path, expected):
    path = Path(path).resolve()
    item = path.stat()
    return _archive(str(path), expected, (item.st_mtime_ns, item.st_size))


def _tree(directory, expected, label, inputs):
    """Snapshot one verified flat source directory; preserve relative names."""
    files = {path.name: path for path in directory.iterdir() if path.is_file()}
    require(set(files) == set(expected), "Native source inventory differs from the pinned source")
    for name, value in expected.items():
        inputs[f"{label}/{name}"] = checked(files[name], value)


def _oqs_inputs(config, inputs):
    hashes = archive_hashes(config.pq_root / "downloads/liboqs-0.16.0.tar.gz", OQS_ARCHIVE)
    source = config.pq_root / "source" / f"liboqs-{OQS_COMMIT}"
    # Installed public headers are copies of the pinned upstream headers, except
    # the exact generated build configuration corresponding to this library.
    by_name = {}
    for path, value in hashes.items():
        by_name.setdefault(Path(path).name, set()).add(value)
    for path in sorted((config.pq_root / "build/include/oqs").glob("*.h")):
        raw = checked(path)
        value = hashlib.sha256(raw).hexdigest()
        require(value == OQS_CONFIG if path.name == "oqsconfig.h" else value in by_name.get(path.name, set()),
                "liboqs header differs from its pinned source/build")
        inputs[f"include/oqs/{path.name}"] = raw
    require("include/oqs/oqs.h" in inputs and "include/oqs/oqsconfig.h" in inputs)
    inputs["libraries/liboqs.a"] = checked(config.pq_root / "build/lib/liboqs.a", OQS_LIBRARY)
    prefix = f"liboqs-{OQS_COMMIT}/src/common/pqclean_shims/"
    shims = {key.removeprefix(prefix): value for key, value in hashes.items()
             if key.startswith(prefix) and "/" not in key.removeprefix(prefix)}
    _tree(source / "src/common/pqclean_shims", shims, "shims", inputs)
    return hashes


def _code_inputs(target, manifest, config, inputs, hashes, closure):
    tier = manifest["instance_size"]
    name = f"{'hqc' if target in HQC_TARGETS else 'mceliece'}-{tier}"
    prefix = f"liboqs-{OQS_COMMIT}/src/kem/" + (
        "hqc/pqc-hqc_hqc-1_ref/" if target in HQC_TARGETS else "classic_mceliece/pqclean_mceliece348864_clean/")
    expected = {key.removeprefix(prefix): value for key, value in hashes.items()
                if key.startswith(prefix) and "/" not in key.removeprefix(prefix)}
    require(expected and name in closure["workers"])
    worker = closure["workers"][name]
    require(set(worker["patched_files"]) <= set(expected))
    expected.update(worker["patched_files"])
    source_root = config.hqc_model_root if target == "hqc-v3" else config.hqc_root if target == "hqc" else config.code_root
    directory = source_root / "source" / name
    _tree(directory, expected, "source", inputs)
    require(closure["base_static_library_sha256"] == OQS_LIBRARY)


def _ntru_inputs(manifest, config, inputs):
    from crucible.ntru_reference import encoded_length
    from crucible.ntru_frontier import PROFILES
    hashes = archive_hashes(config.ntru_archive, NTRU_ARCHIVE)
    prefix = "ntruprime-20201007/Reference_Implementation/kem/sntrup761/"
    source = config.ntru_root / "build"
    expected = {key.removeprefix(prefix): value for key, value in hashes.items()
                if key.startswith(prefix) and key.endswith((".c", ".h")) and
                ("/" not in key.removeprefix(prefix) or key.removeprefix(prefix).startswith("subroutines/"))}
    # Original kem.c is not compiled; the exact reviewed adapted core is.
    expected.pop("params.h", None)
    expected.pop("randombytes.h", None)  # Replaced by the independently pinned OS RNG header.
    for name, value in expected.items():
        inputs[f"source/{name}"] = checked(source / name, value)
    inputs["source/ntru_tier_kem.c"] = checked(source / "ntru_tier_kem.c", NTRU_CORE)
    inputs["source/randombytes.h"] = checked(source / "randombytes.h", RANDOM_HEADER)
    parameters = b"#define p active_p\n#define q active_q\n#define w active_w\n#define Rq_bytes active_pk\n#define Rounded_bytes active_rounded\n"
    require(checked(source / "params.h") == parameters)
    inputs["source/params.h"] = parameters
    table = [[r["p"], r["q"], r["w"], encoded_length(r["p"], r["q"]),
              encoded_length(r["p"], (r["q"] + 2) // 3)] for r in PROFILES]
    header = (f"#define PROFILE_COUNT {len(PROFILES)}\nstatic const int profiles[PROFILE_COUNT][5] = {{\n" +
              ",\n".join("{" + ",".join(map(str, row)) + "}" for row in table) + "\n};\n").encode()
    require(checked(source / "ntru_tier_profiles.h") == header)
    inputs["source/ntru_tier_profiles.h"] = header
    inputs["libraries/librandombytes-kernel.a"] = checked(config.random_library, RANDOM_LIBRARY)
    inputs["libraries/libcrypto.a"] = checked(config.openssl_prefix / "lib/libcrypto.a", CRYPTO_LIBRARY)
    # System-installed OpenSSL declarations are snapshotted and fingerprinted;
    # the cryptographic implementation is the exact archive pinned above.
    for path in sorted((config.openssl_prefix / "include/openssl").glob("*.h")):
        inputs[f"include/openssl/{path.name}"] = checked(path)
    require("include/openssl/sha.h" in inputs)


def prepare(target, manifest, config=None):
    """Return (binary, selector, public build receipt); never run an exchange."""
    require(target in TARGETS, "Unknown trusted native benchmark target")
    config = config or BuildInputs()
    module = adapter(target)
    closure = module.verify_artifact()
    require(closure["artifact_sha256"] == manifest["implementation"]["artifact_sha256"])
    require((module.SCHEME_ID, module.SCHEME_VERSION) == (manifest["scheme_id"], manifest["version"]))
    inputs = {"driver/reference_benchmark_native.c": checked(ROOT / "scripts/reference_benchmark_native.c")}
    family = TARGETS[target]
    flags = ["-std=c99", "-O2" if target == "ntru" else "-O1", f"-DBENCH_FAMILY={family}"]
    if target == "ntru":
        _ntru_inputs(manifest, config, inputs)
        sources = ["source/Decode.c", "source/Encode.c", "source/int32.c", "source/uint32.c",
                   "source/subroutines/crypto_hash_sha512.c", "source/subroutines/crypto_sort_uint32.c"]
        flags += ["-DCRYPTO_NAMESPACE(x)=x", "-Wno-unused-function", "-Wno-unused-parameter"]
        selector = str(manifest["instance_size"])
    else:
        hashes = _oqs_inputs(config, inputs)
        if target in (*HQC_TARGETS, "mceliece"):
            _code_inputs(target, manifest, config, inputs, hashes, closure)
            flags += (["-DHQC_ARCH_REF=1", "-DPQCHQC_NAMESPACE_PREFIX=PQCHQC_HQC1_C_", "-DUSE_OQS_RANDOMBYTES"]
                      if target in HQC_TARGETS else ["-Dold_gas_syntax"])
            sources = sorted(key for key in inputs if key.startswith("source/") and key.endswith(".c"))
            selector = "fixed"
        else:
            sources, selector = [], manifest["parameters"]["parameter_set"]
            require(selector in ("ML-KEM-512", "ML-KEM-768", "ML-KEM-1024"))
    compiler = subprocess.check_output(["cc", "--version"], text=True, timeout=5).splitlines()[0]
    files = {name: hashlib.sha256(value).hexdigest() for name, value in inputs.items()}
    base = {"format": "crucible-native-benchmark-artifact-v1", "target": target,
        "scheme_id": manifest["scheme_id"], "scheme_version": manifest["version"], "profile_id": manifest["id"],
        "parameters_sha256": digest(manifest["parameters"]),
        "adapter_artifact_sha256": closure["artifact_sha256"], "compiler": compiler,
        "build_options": flags, "files": files,
        "measurement_scope": "Native core operation calls with OS randomness; driver buffer allocation and initialization excluded. Different from Python API allocation overhead." + (" ML-KEM uses the pinned liboqs AArch64 backend; it is architecture optimized." if target == "mlkem" else "") }
    # Refuse a directory resolving through the live .native symlinks or elsewhere.
    local_root = ROOT.resolve() / ".local"
    output = config.output.resolve()
    require(not local_root.is_symlink() and output.is_relative_to(local_root) and not config.output.is_symlink(),
            "Native timing builds must stay beneath this checkout's .local directory")
    output.mkdir(parents=True, exist_ok=True)
    directory = output / digest(base)
    require(not directory.is_symlink())
    binary, receipt = directory / "benchmark", directory / "artifact.json"
    if receipt.exists():
        metadata = json.loads(checked(receipt).decode())
        require({key: value for key, value in metadata.items() if key not in {"binary_sha256", "sha256"}} == base)
        require(metadata["sha256"] == digest({key: value for key, value in metadata.items() if key != "sha256"}))
        checked(binary, metadata["binary_sha256"])
        return binary, selector, metadata
    require(not directory.exists(), "Incomplete native timing build exists; inspect it before retrying")
    directory.mkdir()
    for name, raw in inputs.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    command = ["cc", *flags, "-Iinclude", "-Isource", "-Isource/subroutines", "-Ishims",
               "driver/reference_benchmark_native.c", *sources,
               *sorted(key for key in inputs if key.startswith("libraries/")), "-o", "benchmark"]
    try:
        subprocess.run(command, cwd=directory, check=True, capture_output=True, timeout=120)
        require(all(file_digest(directory / name) == value for name, value in files.items()))
        metadata = {**base, "binary_sha256": file_digest(binary)}
        metadata["sha256"] = digest(metadata)
        receipt.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    except BaseException:
        # The directory was newly created here, entirely under the private build root.
        shutil.rmtree(directory)
        raise
    return binary, selector, metadata


def verify_binary(binary, metadata):
    require(metadata["sha256"] == digest({key: value for key, value in metadata.items() if key != "sha256"}))
    checked(binary, metadata["binary_sha256"])
