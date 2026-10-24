"""Extract public NIST vectors from the existing pinned ACVP corpus, offline.

This does not generate expected answers with the implementation under test.
Input files are public test material, including deliberately public test keys.
"""
import argparse
import hashlib
import json
from pathlib import Path

SOURCES = {
    "ML-KEM-keyGen-FIPS203.json": "80a2f859ecdebdba349a55fd3ccb1b8b33ee9c0eab08b171f0ed1a6e3bb45fb9",
    "ML-KEM-encapDecap-FIPS203.json": "e2d53b623c3ff79a52695d72ed10595ee1743ad6260ed0a3ff89d51bb5d474eb",
}
SOURCE_ROOT = "https://raw.githubusercontent.com/usnistgov/ACVP-Server/refs/tags/v1.1.0.41/gen-val/json-files/"


def extract(directory):
    groups = []
    for name, expected in SOURCES.items():
        data = (directory / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("NIST input corpus differs from reviewed hash")
        document = json.loads(data)
        for group in document["testGroups"]:
            if group["parameterSet"] not in ("ML-KEM-512", "ML-KEM-768"):
                continue
            groups.append({"source_file": name, "group_id": group["tgId"],
                           "parameter_set": group["parameterSet"],
                           "function": group.get("function", "keygen"), "tests": group["tests"]})
    return {"format": "crucible-nist-module-v4-fixtures-v1", "authority": "NIST ACVP-Server v1.1.0.41",
            "source_root": SOURCE_ROOT, "source_sha256": SOURCES, "groups": groups,
            "scope": "Every keygen, encapsulation, decapsulation and key-validation case for ML-KEM-512 and ML-KEM-768 in the two pinned files. Conformance observations, not FIPS certification or exhaustive software proof."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = extract(args.corpus)
    with args.output.open("x") as output:
        json.dump(report, output, sort_keys=True, indent=2)
        output.write("\n")
