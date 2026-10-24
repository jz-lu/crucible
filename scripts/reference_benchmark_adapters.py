"""Explicit trusted benchmark targets. This is not a proposal/plugin loader."""
import hashlib
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "dh": ("crucible.dh_v7", "Python finite-field DH reference", "Python"),
    "rsa": ("crucible.rsa_v4", "Python certified-prime RSA reference", "Python"),
    "ec": ("crucible.ec_compressed", "Python compressed-curve reference", "Python"),
    "module": ("crucible.lattice_module_v3", "Python packed module-LWE reference", "Python"),
    "module-v4": ("crucible.lattice_module_v4", "Python compressed module-LWE reference", "Python"),
    "frodo": ("crucible.lattice_frodo_v3", "Python salted Frodo reference", "Python"),
    "hqc": ("crucible.code_hqc_v2", "Pinned HQC growth reference core", "C"),
    "hqc-v3": ("crucible.code_hqc_v3", "Pinned HQC modeled research reference core", "C"),
    "mceliece": ("crucible.code_mceliece", "Pinned Classic McEliece length reference core", "C"),
    "ntru": ("crucible.ntru_frontier", "Pinned Streamlined NTRU Prime frontier reference core", "C"),
    "mlkem": ("crucible.pq_mlkem", "Pinned liboqs ML-KEM AArch64 backend", "C"),
}
PYTHON_TARGETS = frozenset({"dh", "rsa", "ec", "module", "module-v4", "frodo"})
NATIVE_TARGETS = frozenset({"hqc", "hqc-v3", "mceliece", "ntru", "mlkem"})


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def adapter(name):
    if name not in TARGETS:
        raise ValueError("Unknown trusted benchmark target")
    return importlib.import_module(TARGETS[name][0])


def profiles(name, selection="pilot"):
    rows = adapter(name).manifests()
    if selection == "all":
        return rows
    if selection != "pilot":
        raise ValueError("Profile selection must be pilot or all")
    indexes = sorted({0, (len(rows) - 1) // 2, len(rows) - 1})
    return [rows[index] for index in indexes]


def implementation(name):
    _, label, language = TARGETS[name]
    return {"label": label, "language": language, "performance_level": "architecture-optimized" if name == "mlkem" else "reference"}
