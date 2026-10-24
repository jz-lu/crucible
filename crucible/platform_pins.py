"""Trusted platform bindings for unchanged KEM algorithms.

Additional builds are selected by a code-reviewed digest, never by discovering
an installed binary or accepting a participant manifest. This verifies files;
it does not grant database admission or establish mathematical equivalence.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
from types import MappingProxyType

FORMAT = "crucible-platform-pins-v1"
BASELINE_PLATFORM = ("Darwin", "arm64")
ROOT = Path(__file__).resolve().parent
METADATA_ROOT = ROOT / "data" / "platform_builds"
MAX_METADATA_BYTES = 32768
# Add only actual independently reviewed metadata digests. An installed file,
# environment variable or database record cannot extend this trust anchor.
REVIEWED_METADATA = MappingProxyType({
    ("dhkem-x25519", "1", "Linux", "x86_64"): "e7445b7e05172ad0e5f1c34725292ea4296a5fc68990b848ff856a70aef4b256",
    ("sntrup", "1", "Linux", "x86_64"): "3734b32ca8b42c7108dde2066d563b5c4ea2ef9a701896a12bed5cf9a89a7443",
    ("sntrup", "2", "Linux", "x86_64"): "2190d681bd78ca1abb2a89e0949c8f1f15ae26adea442e6f111824805e9aa3d4",
    ("sntrup", "3", "Linux", "x86_64"): "8f9dfb1b0573d1bf9f632f5ea62af10dbb8cb333bcb168a610f6707668c2a1ef",
})
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class Unavailable(ValueError):
    """A platform binding or its bounded files could not be verified."""


def _read(path, limit):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
                raise Unavailable("Platform artifact exceeds its file bound.")
            raw = stream.read(limit + 1)
            if len(raw) != info.st_size or len(raw) > limit:
                raise Unavailable("Platform artifact changed while being read.")
            return raw
    except OSError:
        raise Unavailable("Reviewed platform artifact is unavailable.") from None


def _digest(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        raise Unavailable("Invalid platform artifact digest.")
    return value


def _fields(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise Unavailable("Invalid platform metadata fields.")


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise Unavailable("Duplicate platform metadata fields.")
        value[key] = item
    return value


def _nonfinite(value):
    raise Unavailable("Nonfinite platform metadata is invalid.")


def _text(value, limit):
    if type(value) is not str or not value.strip() or len(value) > limit or "\x00" in value:
        raise Unavailable("Invalid platform build provenance.")


@dataclass(frozen=True)
class Binding:
    pins: object
    system: str
    machine: str
    metadata_sha256: str | None
    build_json: str | None

    @property
    def additional(self):
        return self.metadata_sha256 is not None

    def identity(self):
        return {"platform": {"system": self.system, "machine": self.machine},
                "loader_source_sha256": _SOURCE_SHA256,
                "metadata_sha256": self.metadata_sha256}

    def source_files(self):
        result = {"crucible/platform_pins.py": _SOURCE_SHA256}
        if self.metadata_sha256 is not None:
            result["crucible/data/platform_builds/" + self.metadata_sha256 + ".json"] = self.metadata_sha256
        return result

    def implementation_fields(self):
        result = {"platform_build": self.identity()}
        if self.additional:
            result["admission"] = {"format": "crucible-platform-admission-v1",
                "kind": "reviewed-additional-platform",
                "platform": {"system": self.system, "machine": self.machine}}
        return result

    def build(self):
        return json.loads(self.build_json) if self.build_json is not None else None


def select(scheme_id, version, *, files, legacy):
    """Verify a code-selected platform and every declared actual runtime file.

    ``files`` maps trusted IDs to (Path, maximum bytes). ``legacy`` holds the
    unchanged macOS hashes. Metadata may replace these hashes, never the file
    paths, bounds, IDs or supported construction. Extra source/build files can
    be bound separately in the reviewed metadata's build evidence.
    """
    if hashlib.sha256(_read(Path(__file__), 65536)).hexdigest() != _SOURCE_SHA256:
        raise Unavailable("Platform binding code changed after import.")
    if not files or set(files) != set(legacy):
        raise Unavailable("Platform file declarations disagree.")
    system, machine = platform.system(), platform.machine()
    build_json = None
    checksum = None
    if (system, machine) == BASELINE_PLATFORM:
        pins = dict(legacy)
    else:
        checksum = REVIEWED_METADATA.get((scheme_id, str(version), system, machine))
        if checksum is None:
            raise Unavailable("No reviewed artifact is registered for this platform.")
        _digest(checksum)
        raw = _read(METADATA_ROOT / (checksum + ".json"), MAX_METADATA_BYTES)
        if hashlib.sha256(raw).hexdigest() != checksum:
            raise Unavailable("Platform metadata differs from its reviewed digest.")
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_nonfinite)
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            raise Unavailable("Invalid platform metadata JSON.") from None
        _fields(value, {"format", "scheme_id", "scheme_version", "platform", "files", "build"})
        _fields(value["platform"], {"system", "machine"})
        if (value["format"] != FORMAT or value["scheme_id"] != scheme_id
                or value["scheme_version"] != str(version)
                or value["platform"] != {"system": system, "machine": machine}):
            raise Unavailable("Platform metadata identifies another release or platform.")
        _fields(value["files"], files)
        pins = value["files"]
        build = value["build"]
        _fields(build, {"recipe_sha256", "toolchain", "dependencies", "evidence"})
        _digest(build["recipe_sha256"])
        _text(build["toolchain"], 4096)
        for field in ("dependencies", "evidence"):
            entries = build[field]
            if type(entries) is not dict or len(entries) > 32 or (field == "evidence" and not entries):
                raise Unavailable("Invalid platform build evidence.")
            for name, digest in entries.items():
                _text(name, 200)
                _digest(digest)
        build_json = json.dumps(build, sort_keys=True, separators=(",", ":"))
    for name, (path, limit) in files.items():
        expected = _digest(pins[name])
        if type(limit) is not int or not 0 < limit <= 64 * 1024 * 1024:
            raise Unavailable("Invalid trusted platform file bound.")
        if hashlib.sha256(_read(path, limit)).hexdigest() != expected:
            raise Unavailable("Platform artifact differs from its reviewed pin.")
    return Binding(MappingProxyType(dict(pins)), system, machine, checksum, build_json)
