"""The explicit local feedback helper never silently omits or bypasses failures."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preflight', ROOT / 'tests/preflight.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def test_browse_runs_every_named_file_in_order_and_uses_pinned_node(monkeypatch, capsys):
    seen = []
    def process(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=preflight.NODE_VERSION + '\n')
    monkeypatch.setattr(preflight.subprocess, 'run', process)
    assert preflight.run('browse', '/fixture/node') == 0
    assert seen[0][0] == ['/fixture/node', '--version']
    assert [command for command, kwargs in seen[1:]] == [['/fixture/node', *row[1:]] for row in preflight.PROFILES['browse']]
    assert all(kwargs['cwd'] == ROOT and kwargs['timeout'] == 300 for command, kwargs in seen[1:])
    assert seen[1][0][1] == 'tests/test_read_navigation_frontend.cjs'
    assert preflight.BOUNDARY in capsys.readouterr().out


@pytest.mark.parametrize('failed_at', [1, 4, 8])
def test_stops_at_first_failed_suite_including_last(monkeypatch, failed_at):
    calls = []
    def process(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=19 if len(calls) == failed_at + 1 else 0,
                               stdout=preflight.NODE_VERSION)
    monkeypatch.setattr(preflight.subprocess, 'run', process)
    assert preflight.run('browse') == 1
    assert len(calls) == failed_at + 1


def test_bad_node_version_fails_before_running_suites(monkeypatch):
    calls = []
    def process(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='v26.0.0\n')
    monkeypatch.setattr(preflight.subprocess, 'run', process)
    assert preflight.run('browse') == 1
    assert calls == [['node', '--version']]


def test_missing_profile_file_fails_before_any_execution(monkeypatch):
    monkeypatch.setitem(preflight.PROFILES, 'broken', [('node', 'tests/nonexistent.cjs')])
    monkeypatch.setattr(preflight.subprocess, 'run', lambda *a, **k: pytest.fail('Must validate all files first'))
    assert preflight.run('broken') == 1


def test_release_profile_uses_current_python_and_no_node(monkeypatch):
    calls = []
    def process(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(preflight.subprocess, 'run', process)
    assert preflight.run('release') == 0
    assert calls == [[preflight.sys.executable, *preflight.PROFILES['release'][0][1:]]]


def test_list_is_read_only_and_names_complete_profiles(monkeypatch, capsys):
    monkeypatch.setattr(preflight.sys, 'argv', ['preflight', '--list'])
    monkeypatch.setattr(preflight, 'run', lambda *a, **k: pytest.fail('Listing must not run checks'))
    assert preflight.main() == 0
    output = capsys.readouterr().out
    for profile, commands in preflight.PROFILES.items():
        assert profile + ':' in output
        for command in commands: assert ' '.join(command) in output
