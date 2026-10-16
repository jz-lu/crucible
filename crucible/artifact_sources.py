"""Allowlisted, bounded public source files from registered artifact closures."""
from pathlib import Path, PurePosixPath
import hashlib
import re

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 2 * 1024 * 1024


def source_files(artifact):
    values = artifact.get("files", {})
    if isinstance(values, list):
        pairs = [(row["path"], row["sha256"]) for row in values]
        values = dict(pairs)
        if len(values) != len(pairs):
            raise ValueError("Duplicate public source paths")
    if not isinstance(values, dict) or len(values) > 32:
        raise ValueError("Invalid public source closure")
    result = {}
    for name, digest in values.items():
        if type(name) is not str:
            raise ValueError("Invalid public source path")
        path = PurePosixPath(name)
        if (type(name) is not str or path.is_absolute() or ".." in path.parts or "\\" in name
                or str(path) != name or not path.parts or path.parts[0] not in {"crucible", "scripts", "data"}
                or path.suffix not in {".py", ".c", ".h", ".json", ".txt", ".sage"}
                or type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
            raise ValueError("Invalid public source path or pin")
        result[name] = digest
    return result


def read_source(artifact, name):
    files = source_files(artifact)
    if name not in files:
        raise KeyError("Not a registered public source artifact")
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT) or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Public source outside its declared boundary")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != files[name]:
        raise ValueError("Public source differs from its registered pin")
    return content
