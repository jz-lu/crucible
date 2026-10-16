#!/usr/bin/env python3
"""Inspect a downloaded challenge. This is not a shared-secret recovery attack.

Use `crucible init --scheme SCHEME --profile-id PROFILE --directory PATH` to
download the current challenge and create an attack workspace. Implement your
method in that workspace and submit the complete recovered KEM shared secret.
"""
import json
import sys
from pathlib import Path


if __name__ == "__main__":
    value = json.loads(Path(sys.argv[1]).read_text())
    manifest = value["scheme_manifest"]
    print(manifest["scheme_id"], manifest["version"], manifest["profile_label"])
    print("Public encoding:", manifest["encoding"])
    print("Recovery objective:", manifest["shared_secret_bytes"], "complete bytes")
