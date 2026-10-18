"""Offline reference-operation measurements; never publishes or touches site data.

Example: python -m scripts.reference_benchmark --targets dh module --output report.json
Only explicit built-in targets run. Each profile gets a bounded child process;
it emits operation durations/outcomes, never keys, ciphertexts or secret seeds.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import selectors
import signal
import statistics
import subprocess
import sys
import time
from uuid import uuid4

from scripts.reference_benchmark_adapters import (ROOT, TARGETS, PYTHON_TARGETS, NATIVE_TARGETS, adapter, canonical,
                                                digest, file_digest, implementation, profiles)

OPERATIONS = ("keygen", "encapsulate", "decapsulate")
OUTCOMES = ("agreement", "agreement_failure", "abort", "error", "timeout")
HARNESS_FILES = ("scripts/reference_benchmark.py", "scripts/reference_benchmark_adapters.py",
                 "scripts/reference_benchmark_worker.py", "scripts/reference_benchmark_native.py",
                 "scripts/reference_benchmark_native.c")
MAX_OUTPUT = 4 * 1024 * 1024


def summarize(samples):
    if not samples:
        return {"count": 0, "samples_ns": [], **{key: None for key in ("median_ns", "q1_ns", "q3_ns", "min_ns", "max_ns")}}
    q1, _, q3 = statistics.quantiles(samples, n=4, method="inclusive") if len(samples) > 1 else (samples[0],) * 3
    return {"count": len(samples), "samples_ns": samples, "median_ns": statistics.median(samples),
            "q1_ns": q1, "q3_ns": q3, "min_ns": min(samples), "max_ns": max(samples)}


class Events:
    """Validate the worker's tiny protocol before retaining any data."""
    def __init__(self, warmups, trials, identity=None):
        self.identity = identity
        self.limits = {"warmup": warmups, "measured": trials}
        self.trials = []
        self.setup_ns = None
        self.failure = None
        self.current = None

    def accept(self, raw):
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Invalid worker event")
        event = value.get("event")
        if event == "setup":
            if self.setup_ns is not None or self.trials or set(value) != {"event", "duration_ns", "source_sha256", "parameters_sha256"}:
                raise ValueError("Invalid setup event")
            if self.identity is not None and any(value[key] != self.identity[key] for key in ("source_sha256", "parameters_sha256")):
                raise ValueError("Worker identity mismatch")
            self.setup_ns = self.duration(value["duration_ns"])
            return
        if event == "failure":
            if set(value) != {"event", "code"} or value["code"] not in {"identity_mismatch", "worker_error"}:
                raise ValueError("Invalid failure event")
            self.failure = value["code"]
            return
        if self.setup_ns is None:
            raise ValueError("Missing worker setup")
        phase, index = value.get("phase"), value.get("index")
        if phase not in self.limits or type(index) is not int or not 0 <= index < self.limits[phase]:
            raise ValueError("Invalid trial identity")
        if event == "begin":
            if set(value) != {"event", "phase", "index"} or self.current is not None:
                raise ValueError("Invalid trial start")
            previous = [(row["phase"], row["index"]) for row in self.trials]
            expected = [(p, i) for p in ("warmup", "measured") for i in range(self.limits[p])]
            if (phase, index) != expected[len(previous)]:
                raise ValueError("Out of order trial")
            self.current = {"phase": phase, "index": index, "outcome": "timeout", "timings_ns": {}}
            self.trials.append(self.current)
            return
        if self.current is None or (phase, index) != (self.current["phase"], self.current["index"]):
            raise ValueError("Operation outside current trial")
        if event == "operation":
            if set(value) != {"event", "phase", "index", "operation", "duration_ns"}:
                raise ValueError("Invalid operation event")
            timings = self.current["timings_ns"]
            if len(timings) >= 3 or value["operation"] != OPERATIONS[len(timings)]:
                raise ValueError("Out of order operation")
            timings[value["operation"]] = self.duration(value["duration_ns"])
        elif event == "outcome":
            if set(value) != {"event", "phase", "index", "outcome"} or value["outcome"] not in OUTCOMES[:-1]:
                raise ValueError("Invalid outcome event")
            if value["outcome"] in {"agreement", "agreement_failure"} and len(self.current["timings_ns"]) != 3:
                raise ValueError("Incomplete exchange cannot claim agreement")
            self.current["outcome"] = value["outcome"]
            self.current = None
        else:
            raise ValueError("Unknown worker event")

    @staticmethod
    def duration(value):
        if type(value) is not int or not 0 <= value <= 120_000_000_000:
            raise ValueError("Invalid duration")
        return value


def collect(command, warmups, trials, timeout, identity=None):
    events = Events(warmups, trials, identity)
    before = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
        env={"PATH": "/usr/bin:/bin", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
    pending, total, timed_out, protocol_error = bytearray(), 0, False, False
    try:
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = timeout - (time.monotonic() - before)
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(min(remaining, .25)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > MAX_OUTPUT:
                        raise ValueError("Worker output bound exceeded")
                    pending.extend(chunk)
                    while b"\n" in pending:
                        line, _, pending = pending.partition(b"\n")
                        if len(line) > 4096:
                            raise ValueError("Worker event bound exceeded")
                        events.accept(line)
                    if len(pending) > 4096:
                        raise ValueError("Worker event bound exceeded")
            if not timed_out:
                try:
                    process.wait(timeout=max(.001, timeout - (time.monotonic() - before)))
                except subprocess.TimeoutExpired:
                    timed_out = True
            if pending and not timed_out:
                raise ValueError("Incomplete worker event")
    except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError):
        protocol_error = True
    finally:
        if timed_out or protocol_error or process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        process.stdout.close()
    if events.current is not None and not timed_out:
        events.current["outcome"] = "error"
    return events, time.monotonic() - before, timed_out, protocol_error, process.returncode


def record(target, manifest, warmups, trials, timeout, *, native_inputs=None):
    if target not in TARGETS:
        raise ValueError("Unknown trusted benchmark target")
    module = adapter(target)
    if hasattr(module, "verify_artifact"):
        module.verify_artifact()
    row = {"scheme_id": manifest["scheme_id"], "version": manifest["version"], "profile_id": manifest["id"],
           "parameters_sha256": digest(manifest["parameters"]),
           "source_sha256": manifest["implementation"]["source_sha256"],
           "artifact_sha256": manifest["implementation"].get("artifact_sha256"),
           "benchmark_artifact": None, "implementation": implementation(target),
           "requested_trials": trials, "warmup_requested": warmups, "notes": []}
    if target in PYTHON_TARGETS:
        command = [sys.executable, "-I", "-B", str(ROOT / "scripts/reference_benchmark_worker.py"),
               "--target", target, "--profile", manifest["id"], "--source-sha256", row["source_sha256"],
               "--parameters-sha256", row["parameters_sha256"], "--warmups", str(warmups),
               "--trials", str(trials), "--seconds", str(timeout)]
        if row["artifact_sha256"] is not None:
            command.extend(["--artifact-sha256", row["artifact_sha256"]])
    else:
        from scripts.reference_benchmark_native import prepare, verify_binary
        binary, selector, row["benchmark_artifact"] = prepare(target, manifest, native_inputs)
        verify_binary(binary, row["benchmark_artifact"])
        command = [str(binary), selector, str(warmups), str(trials), str(math.ceil(timeout)),
                   row["source_sha256"], row["parameters_sha256"]]
        row["notes"].append(row["benchmark_artifact"]["measurement_scope"])
    events, elapsed, timed_out, protocol_error, exit_code = collect(command, warmups, trials, timeout, row)
    module = adapter(target)
    try:
        if hasattr(module, "verify_artifact"):
            module.verify_artifact()
        current = module.get_manifest(manifest["id"])
        source_changed = (file_digest(module.__file__) != row["source_sha256"] or
                          digest(current["parameters"]) != row["parameters_sha256"] or
                          current["implementation"].get("artifact_sha256") != row["artifact_sha256"])
        if target in NATIVE_TARGETS:
            verify_binary(binary, row["benchmark_artifact"])
    except Exception:
        source_changed = True
    row.update(timed_out=timed_out, protocol_error=protocol_error, worker_failure=events.failure,
               exit_code=exit_code, source_changed=source_changed)
    counts = {phase: dict(Counter(trial["outcome"] for trial in events.trials if trial["phase"] == phase))
              for phase in ("warmup", "measured")}
    complete = lambda phase: sum(counts[phase].get(outcome, 0) for outcome in ("agreement", "agreement_failure"))
    status = "complete"
    if protocol_error or events.failure or any(counts[p].get(o, 0) for p in counts for o in ("error", "agreement_failure")):
        status = "failed"
    elif timed_out or exit_code or complete("measured") != trials or complete("warmup") != warmups:
        status = "partial"
    if timed_out:
        row["notes"].append("Parent deadline reached; completed operations retained without extrapolation.")
    if protocol_error:
        row["notes"].append("Worker output rejected; record is not publishable.")
    if events.failure:
        row["notes"].append(events.failure)
    row.update(status=status, setup_seconds=None if events.setup_ns is None else events.setup_ns / 1e9,
               end_to_end_seconds=elapsed, completed_trials=complete("measured"), warmup_completed=complete("warmup"),
               outcomes=counts, trials=events.trials,
               operations={operation: summarize([trial["timings_ns"][operation] for trial in events.trials
                           if trial["phase"] == "measured" and operation in trial["timings_ns"]]) for operation in OPERATIONS})
    if source_changed:
        row["status"] = "failed"
        row["notes"].append("Implementation closure changed during measurement.")
    return row


def environment():
    cpu = platform.processor() or platform.machine()
    if sys.platform == "darwin":
        result = subprocess.run(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            cpu = result.stdout.strip()
    elif Path("/proc/cpuinfo").is_file():
        cpu = next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")), cpu)
    return {"cpu_model": cpu, "architecture": platform.machine(), "os": platform.platform(),
            "python": platform.python_implementation() + " " + platform.python_version(),
            "single_threaded": True, "cpu_affinity": "not pinned"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", nargs="+", choices=sorted(TARGETS), default=["dh", "module"])
    parser.add_argument("--selection", choices=("pilot", "all"), default="pilot")
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--profile-timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prepare-only", action="store_true", help="Build/verify native drivers without running exchanges")
    from scripts.reference_benchmark_native import BuildInputs
    for name in BuildInputs.__dataclass_fields__:
        parser.add_argument("--native-" + name.replace("_", "-"), type=Path,
                            default=getattr(BuildInputs(), name))
    args = parser.parse_args()
    if not (0 <= args.warmups <= 10 and 1 <= args.trials <= 1000 and math.isfinite(args.profile_timeout) and 1 <= args.profile_timeout <= 120):
        parser.error("Use 0..10 warmups, 1..1000 trials and a 1..120 second profile timeout")
    if not args.prepare_only and args.output is None:
        parser.error("--output is required unless using --prepare-only")
    if args.output is not None and args.output.exists():
        parser.error("Output already exists; choose a new evidence file")
    native_inputs = BuildInputs(**{name: getattr(args, "native_" + name) for name in BuildInputs.__dataclass_fields__})
    if args.prepare_only:
        from scripts.reference_benchmark_native import prepare
        for target in dict.fromkeys(args.targets):
            if target in NATIVE_TARGETS:
                for manifest in profiles(target, args.selection):
                    _, _, artifact = prepare(target, manifest, native_inputs)
                    print(json.dumps({"prepared": manifest["id"], "artifact_sha256": artifact["sha256"]}), flush=True)
        return
    files = {name: file_digest(ROOT / name) for name in HARNESS_FILES}
    report = {"format": "crucible-reference-benchmark-v1", "method_id": "reference-operations-v1",
              "run_id": str(uuid4()), "measured_at": datetime.now(timezone.utc).isoformat(),
              "environment": environment(), "harness": {"files": files, "sha256": digest(files)},
              "policy": {"warmups": args.warmups, "trials": args.trials, "profile_timeout_seconds": args.profile_timeout,
                         "includes": ["required operation checks", "RNG", "canonical cryptographic encoding", "KDF"],
                         "excludes": ["process startup", "artifact verification", "HTTP", "JSON", "database", "final equality check"],
                         "input_distribution": "fresh independent honest keypair and encapsulation per trial; valid ciphertext decapsulation",
                         "quartiles": "inclusive linear interpolation; summaries retain completed operations from partial trials"},
              "records": []}
    for target in dict.fromkeys(args.targets):
        for manifest in profiles(target, args.selection):
            report["records"].append(record(target, manifest, args.warmups, args.trials, args.profile_timeout,
                                             native_inputs=native_inputs))
    # Only public numeric evidence is persisted, and publication remains manual.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"report": str(args.output), "records": len(report["records"]),
                      "statuses": dict(Counter(row["status"] for row in report["records"]))}))


if __name__ == "__main__":
    main()
