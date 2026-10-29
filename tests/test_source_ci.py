"""Exact-commit release source evidence, with synthetic read-only GitHub responses."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('source_ci', ROOT / 'deploy/source_ci.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
SHA = 'a' * 40
WORKFLOW_ID = 12


def run(**changes):
    return {'id': 301, 'run_attempt': 1, 'workflow_id': WORKFLOW_ID,
            'event': 'push', 'path': ci.WORKFLOW, 'head_sha': SHA, 'head_branch': 'main',
            'repository': {'full_name': ci.REPOSITORY}, 'head_repository': {'full_name': ci.REPOSITORY},
            'status': 'completed', 'conclusion': 'success', **changes}


def jobs(value=None):
    value = value or run()
    rows = [{'id': n + 1, 'name': name, 'run_id': value['id'], 'run_attempt': value['run_attempt'],
             'head_sha': SHA, 'status': 'completed', 'conclusion': 'success'}
            for n, name in enumerate(sorted(ci.EXPECTED_JOBS))]
    return {'total_count': len(rows), 'jobs': rows}


class API:
    def __init__(self, runs=None, job_data=None):
        self.snapshots = runs or [[run()], [run()]]
        self.job_data = jobs() if job_data is None else job_data
        self.paths = []

    def __call__(self, path):
        self.paths.append(path)
        if path.endswith('/actions/workflows/tests.yml'):
            return {'id': WORKFLOW_ID, 'path': ci.WORKFLOW, 'state': 'active'}
        if '/runs?' in path:
            values = self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]
            return {'total_count': len(values), 'workflow_runs': values}
        assert path.endswith('/runs/301/attempts/1/jobs?per_page=100')
        return deepcopy(self.job_data)


def test_accepts_all_gates_from_one_exact_trusted_attempt_and_records_link():
    api = API()
    result = ci.verify(SHA, read=api)
    assert result['status'] == 'passed' and result['source_revision'] == SHA
    assert result['run_id'] == 301 and result['run_attempt'] == 1
    assert result['gates'] == sorted(ci.EXPECTED_JOBS)
    assert result['url'] == 'https://github.com/jz-lu/crucible/actions/runs/301'
    assert len([p for p in api.paths if '/runs?' in p]) == 2


@pytest.mark.parametrize('changes', [
    {'head_sha': 'b' * 40}, {'head_branch': 'feature'}, {'event': 'pull_request'},
    {'event': 'pull_request_target'}, {'event': 'workflow_dispatch'},
    {'workflow_id': 99}, {'path': '.github/workflows/forged.yml'},
    {'repository': {'full_name': 'fork/crucible'}}, {'head_repository': {'full_name': 'fork/crucible'}},
    {'head_repository': None}, {'run_attempt': 0}, {'id': True},
    {'status': 'completed', 'conclusion': 'failure'}, {'conclusion': 'cancelled'},
    {'conclusion': 'skipped'}, {'conclusion': 'neutral'}, {'status': 'in_progress'},
])
def test_rejects_wrong_identity_or_nonpassing_run(changes):
    with pytest.raises(ci.GateError):
        ci.validate_success(run(**changes), jobs(), SHA, WORKFLOW_ID)


@pytest.mark.parametrize('name', sorted(ci.EXPECTED_JOBS))
@pytest.mark.parametrize('status', ['failure', 'cancelled', 'skipped', 'neutral', None])
def test_every_job_including_each_matrix_leg_and_aggregate_must_pass(name, status):
    value = jobs()
    next(row for row in value['jobs'] if row['name'] == name)['conclusion'] = status
    with pytest.raises(ci.GateError, match='Every expected gate'):
        ci.validate_success(run(), value, SHA, WORKFLOW_ID)


@pytest.mark.parametrize('change', ['missing', 'extra', 'duplicate_name', 'duplicate_id', 'pagination',
                                    'old_attempt', 'old_run', 'other_sha', 'in_progress'])
def test_rejects_incomplete_ambiguous_or_mixed_job_evidence(change):
    value = jobs()
    if change == 'missing': value['jobs'].pop(); value['total_count'] -= 1
    elif change == 'extra': value['jobs'].append({**value['jobs'][0], 'id': 100, 'name': 'unexpected'}); value['total_count'] += 1
    elif change == 'duplicate_name': value['jobs'][0]['name'] = value['jobs'][1]['name']
    elif change == 'duplicate_id': value['jobs'][0]['id'] = value['jobs'][1]['id']
    elif change == 'pagination': value['total_count'] += 1
    else:
        key, replacement = {'old_attempt': ('run_attempt', 2), 'old_run': ('run_id', 100),
                            'other_sha': ('head_sha', 'b' * 40), 'in_progress': ('status', 'in_progress')}[change]
        value['jobs'][0][key] = replacement
    with pytest.raises(ci.GateError): ci.validate_success(run(), value, SHA, WORKFLOW_ID)


def test_latest_failed_run_never_falls_back_to_an_older_success():
    api = API(runs=[[run(id=200), run(conclusion='failure')]])
    with pytest.raises(ci.GateError, match='Latest exact main CI failed'):
        ci.verify(SHA, read=api)
    assert not any('/jobs?' in p for p in api.paths)


def test_waits_for_missing_then_pending_exact_ci_without_starting_any_jobs():
    api = API(runs=[[], [run(status='in_progress', conclusion=None)], [run()], [run()]])
    times = []
    result = ci.verify(SHA, read=api, sleep=times.append)
    assert result['status'] == 'passed' and times == [15, 15]
    assert all(path.startswith('repos/jz-lu/crucible/actions/') for path in api.paths)


def test_missing_ci_times_out_without_reusing_pr_or_starting_a_fallback():
    api = API(runs=[[]])
    with pytest.raises(ci.GateError, match='Timed out'):
        ci.verify(SHA, read=api, wait_seconds=0)


@pytest.mark.parametrize('changed', [run(id=302), run(run_attempt=2), run(status='in_progress', conclusion=None)])
def test_ci_rerun_or_replacement_during_read_blocks_release(changed):
    with pytest.raises(ci.GateError, match='changed during verification'):
        ci.verify(SHA, read=API(runs=[[run()], [changed]]))


def test_no_other_workflow_or_truncated_inventory_is_accepted():
    def missing(path): return {'id': 12, 'path': ci.WORKFLOW, 'state': 'disabled_manually'}
    with pytest.raises(ci.GateError, match='unavailable'): ci.verify(SHA, read=missing)
    with pytest.raises(ci.GateError, match='Incomplete'):
        ci.latest_run(SHA, lambda path: {'total_count': 101, 'workflow_runs': [run()]})


def test_api_uses_only_get_and_fixed_host_and_never_prints_provider_errors(monkeypatch):
    calls = []
    def process(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'{"ok":true}', stderr=b'')
    monkeypatch.setattr(ci.subprocess, 'run', process)
    assert ci.api('repos/jz-lu/crucible/actions/workflows/tests.yml') == {'ok': True}
    command, kwargs = calls[0]
    assert command[:7] == ['gh', 'api', '--hostname', 'github.com', '--method', 'GET', '-H']
    assert kwargs['capture_output'] and kwargs['timeout'] == 30
    with pytest.raises(ci.GateError, match='Unexpected API path'):
        ci.api('https://other.invalid')
    monkeypatch.setattr(ci.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout=b'', stderr=b'secret'))
    with pytest.raises(ci.GateError, match='Cannot read trusted CI') as error:
        ci.api('repos/jz-lu/crucible/actions/workflows/tests.yml')
    assert 'secret' not in str(error.value)


def test_expected_gate_inventory_matches_actual_workflow_including_matrix():
    # The explicit inventory must be reviewed whenever CI names/matrix change.
    # The online validator separately rejects any additional/missing jobs.
    text = (ROOT / ci.WORKFLOW).read_text()
    names = re.findall(r'^    name: (.+)$', text, re.M)
    versions = re.search(r"python-version: \[([^]]+)\]", text)[1]
    expanded = set()
    for name in names:
        if '${{ matrix.python-version }}' in name:
            expanded.update(name.replace('${{ matrix.python-version }}', v) for v in re.findall(r"'([^']+)'", versions))
        else: expanded.add(name)
    assert expanded == ci.EXPECTED_JOBS
    assert 'tests/test_source_ci.py' in text


def test_main_gate_precedes_registry_authentication_and_native_checks_are_preserved():
    workflow = (ROOT / '.github/workflows/release-beta.yml').read_text()
    assert workflow.index('Set isolated release directories') < workflow.index('Require trusted source CI')
    assert workflow.index('Require trusted source CI') < workflow.index('Obtain one-hour private registry credential')
    assert 'actions: read' in workflow and 'actions: write' not in workflow
    assert 'GH_TOKEN: ${{ github.token }}' in workflow
    assert 'crucible-source-ci.json' in workflow
    assert 'uses: ./.github/workflows/tests.yml' in workflow
    assert 'deploy/release.py check-image --image "$IMAGE"' in workflow


@pytest.mark.parametrize('field,value', [('GITHUB_REPOSITORY', 'fork/crucible'),
                                        ('GITHUB_REF', 'refs/heads/candidate'),
                                        ('GITHUB_SHA', 'b' * 40)])
def test_cli_rejects_another_release_context_before_any_reads(tmp_path, monkeypatch, field, value):
    for key, expected in {'GITHUB_REPOSITORY': ci.REPOSITORY, 'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': SHA}.items():
        monkeypatch.setenv(key, expected)
    monkeypatch.setenv(field, value)
    monkeypatch.setattr(ci.sys, 'argv', ['source_ci', '--revision', SHA, '--output', str(tmp_path / 'receipt')])
    monkeypatch.setattr(ci, 'verify', lambda *a, **k: pytest.fail('CI must not be read for a foreign context'))
    with pytest.raises(ci.GateError, match='Exact main release context'):
        ci.main()
    assert not (tmp_path / 'receipt').exists()


def test_cli_binds_checkout_and_writes_fresh_nonsecret_receipt(tmp_path, monkeypatch):
    for key, expected in {'GITHUB_REPOSITORY': ci.REPOSITORY, 'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': SHA}.items():
        monkeypatch.setenv(key, expected)
    output = tmp_path / 'receipt'
    monkeypatch.setattr(ci.sys, 'argv', ['source_ci', '--revision', SHA, '--output', str(output)])
    monkeypatch.setattr(ci.subprocess, 'check_output', lambda *a, **k: (SHA + '\n').encode())
    monkeypatch.setattr(ci, 'verify', lambda *a, **k: {'status': 'passed', 'url': 'https://github.com/jz-lu/crucible/actions/runs/301', 'run_attempt': 1})
    ci.main()
    assert json.loads(output.read_text())['workflow_sha256']
    original = output.read_bytes()
    with pytest.raises(ci.GateError, match='fresh source CI receipt'):
        ci.main()
    assert output.read_bytes() == original
    output.unlink()
    monkeypatch.setattr(ci.subprocess, 'check_output', lambda *a, **k: ('b' * 40).encode())
    with pytest.raises(ci.GateError, match='Checkout differs'):
        ci.main()
    assert not output.exists()
