"""Private benchmark process: only bounded numeric events leave stdout."""
import argparse
import hmac
import json
import math
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.reference_benchmark_adapters import PYTHON_TARGETS, adapter, digest, file_digest


def emit(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)


def limits(seconds):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(seconds) + 2, math.ceil(seconds) + 2))
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))


def run(args):
    limits(args.seconds)
    started = time.perf_counter_ns()
    module = adapter(args.target)
    manifest = module.get_manifest(args.profile)
    if hasattr(module, "verify_artifact"):
        module.verify_artifact()
    if (file_digest(module.__file__) != args.source_sha256 or
            manifest["implementation"]["source_sha256"] != args.source_sha256 or
            digest(manifest["parameters"]) != args.parameters_sha256 or
            manifest["implementation"].get("artifact_sha256") != args.artifact_sha256):
        emit({"event": "failure", "code": "identity_mismatch"})
        return 2
    emit({"event": "setup", "duration_ns": time.perf_counter_ns() - started,
          "source_sha256": args.source_sha256, "parameters_sha256": args.parameters_sha256})
    for phase, count in (("warmup", args.warmups), ("measured", args.trials)):
        for index in range(count):
            emit({"event": "begin", "phase": phase, "index": index})
            try:
                before = time.perf_counter_ns()
                pk, sk = module.keygen(args.profile)
                elapsed = time.perf_counter_ns() - before
                emit({"event": "operation", "phase": phase, "index": index, "operation": "keygen",
                      "duration_ns": elapsed})
                before = time.perf_counter_ns()
                ct, secret = module.encapsulate(args.profile, pk)
                elapsed = time.perf_counter_ns() - before
                emit({"event": "operation", "phase": phase, "index": index, "operation": "encapsulate",
                      "duration_ns": elapsed})
                before = time.perf_counter_ns()
                recovered = module.decapsulate(args.profile, sk, ct)
                elapsed = time.perf_counter_ns() - before
                emit({"event": "operation", "phase": phase, "index": index, "operation": "decapsulate",
                      "duration_ns": elapsed})
                outcome = "agreement" if hmac.compare_digest(secret, recovered) else "agreement_failure"
                del pk, sk, ct, secret, recovered
            except Exception as error:
                # No exception text, objects, keys or ciphertexts enter evidence.
                kinds = tuple(kind for name in ("PrimeGenerationLimit", "GenerationLimit", "GenerationResourceLimit")
                              if isinstance(kind := getattr(module, name, None), type))
                outcome = "abort" if kinds and isinstance(error, kinds) else "error"
            emit({"event": "outcome", "phase": phase, "index": index, "outcome": outcome})
            if outcome in {"error", "agreement_failure"}:
                return 1
    if hasattr(module, "verify_artifact"):
        module.verify_artifact()
    if file_digest(module.__file__) != args.source_sha256 or module.get_manifest(args.profile)["implementation"].get("artifact_sha256") != args.artifact_sha256:
        emit({"event": "failure", "code": "identity_mismatch"})
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(PYTHON_TARGETS), required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--parameters-sha256", required=True)
    parser.add_argument("--artifact-sha256", default=None)
    parser.add_argument("--warmups", type=int, required=True)
    parser.add_argument("--trials", type=int, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    args = parser.parse_args()
    if not (0 <= args.warmups <= 10 and 1 <= args.trials <= 1000 and 1 <= args.seconds <= 120):
        return 2
    try:
        return run(args)
    except BaseException:
        emit({"event": "failure", "code": "worker_error"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
