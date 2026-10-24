"""Build-only deadline policy and real subprocess-group cleanup checks."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sntrup_builder", ROOT / "scripts/build_sntrup.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.mark.parametrize("value", ["59", "3601", "-1", "1.5", "nan", ""])
def test_cli_rejects_unbounded_or_noninteger_timeout_before_build(tmp_path, value):
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_sntrup.py"),
                             "--source-cache", str(tmp_path / "cache"),
                             "--build-timeout-seconds", value], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "whole number from 60 to 3600" in result.stderr
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("requested", [None, "60", "3600"])
def test_option_applies_only_to_make_install(tmp_path, monkeypatch, requested):
    empty_archive = tmp_path / "source.tar"
    with tarfile.open(empty_archive, "w"):
        pass
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "SOURCES", (("libntruprime-fixture", "fixture://source", "a" * 64),))
    monkeypatch.setattr(builder, "archive", lambda *_: empty_archive)
    monkeypatch.setattr(builder, "digest", lambda _: "b" * 64)
    monkeypatch.setattr(builder.subprocess, "check_output", lambda *a, **kw: "fixture compiler\n")
    calls = []
    monkeypatch.setattr(builder, "run", lambda args, cwd, env, log, **kw: calls.append((args, kw)))
    argv = ["build_sntrup.py", "--reference", "--source-cache", str(tmp_path / "cache")]
    if requested is not None:
        argv += ["--build-timeout-seconds", requested]
    monkeypatch.setattr(sys, "argv", argv)
    builder.main()
    expected = int(requested) if requested is not None else 900
    assert len(calls) == 5  # configure, make, worker, published-profile test, reference
    assert [kw for args, kw in calls if args[0] == "make"] == [{"timeout_seconds": expected}]
    assert all(not kw for args, kw in calls if args[0] != "make")
    record = json.loads((tmp_path / ".native/sntrup/build-manifest.json").read_text())
    assert record["make_install_timeout_seconds"] == expected


def active_process(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A terminated orphan can briefly await its host init's reap. Zombies are
    # no longer running and cannot consume compile resources.
    if sys.platform.startswith("linux"):
        try:
            status = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return False
        return status != "Z"
    return True


@pytest.mark.skipif(os.name != "posix", reason="Native build uses POSIX process groups")
@pytest.mark.parametrize("leader_ignores_term", [False, True])
def test_timeout_terminates_entire_build_tree(tmp_path, leader_ignores_term):
    leaf_file, child_file, leader_file = (tmp_path / name for name in ("leaf.pid", "child.pid", "leader.pid"))
    leaf = ("import os,signal,time,pathlib\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"pathlib.Path({str(leaf_file)!r}).write_text(str(os.getpid()))\n"
            "while True: time.sleep(1)\n")
    child = ("import os,signal,subprocess,sys,time,pathlib\n"
             "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
             f"pathlib.Path({str(child_file)!r}).write_text(str(os.getpid()))\n"
             f"subprocess.Popen([sys.executable, '-c', {leaf!r}])\n"
             "while True: time.sleep(1)\n")
    leader = ("import os,signal,subprocess,sys,time,pathlib\n"
              + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if leader_ignores_term else "")
              + f"pathlib.Path({str(leader_file)!r}).write_text(str(os.getpid()))\n"
              + f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
              + "while True: time.sleep(1)\n")
    started = time.monotonic()
    try:
        with (tmp_path / "build.log").open("w") as log:
            with pytest.raises(subprocess.TimeoutExpired):
                builder.run([sys.executable, "-c", leader], tmp_path, os.environ.copy(), log,
                            timeout_seconds=1.5)
        assert time.monotonic() - started < 7
        pids = [int(path.read_text()) for path in (leader_file, child_file, leaf_file)]
        deadline = time.monotonic() + 3
        while any(active_process(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not any(active_process(pid) for pid in pids)
    finally:
        # Never leave synthetic processes behind if a future regression fails.
        for path in (leader_file, child_file, leaf_file):
            if path.exists():
                try:
                    os.kill(int(path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_run_preserves_failure_and_log(tmp_path):
    with (tmp_path / "build.log").open("w") as log:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            builder.run([sys.executable, "-c", "print('compile failure', flush=True); raise SystemExit(7)"],
                        tmp_path, os.environ.copy(), log, timeout_seconds=3)
    assert caught.value.returncode == 7
    assert (tmp_path / "build.log").read_text().strip() == "compile failure"
