"""Trusted artifact reuse; all GitHub/registry interaction is synthetic."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from deploy import reuse_release as reuse

SHA = 'a' * 40


@pytest.fixture
def evidence():
    repository = {'full_name': 'jz-lu/crucible', 'id': 11}
    run = {'id': 42, 'run_attempt': 2, 'workflow_id': 17, 'path': reuse.WORKFLOW,
           'event': 'workflow_dispatch', 'head_branch': 'main', 'head_sha': SHA,
           'status': 'completed', 'conclusion': 'success',
           'repository': repository, 'head_repository': repository}
    steps = [{'name': name, 'status': 'completed', 'conclusion': conclusion} for name, conclusion in (
        ('Verify exact native catalogue and application regressions', 'success'),
        ('Publish the tested image', 'success'),
        ('Promote the exact tested image to beta', 'skipped'))]
    job = {'id': 99, 'run_id': 42, 'run_attempt': 2, 'head_sha': SHA,
           'name': 'Build and verify native beta image', 'status': 'completed',
           'conclusion': 'success', 'steps': steps}
    artifact = {'id': 71, 'name': f'beta-release-{SHA}-2', 'expired': False, 'size_in_bytes': 2048,
                'workflow_run': {'id': 42, 'repository_id': 11, 'head_repository_id': 11,
                                 'head_branch': 'main', 'head_sha': SHA}}
    return {
        'workflows/release-beta.yml': {'id': 17, 'path': reuse.WORKFLOW, 'state': 'active'},
        'runs/42': run,
        'runs/42/attempts/2/jobs?per_page=100': {'total_count': 1, 'jobs': [job]},
        'runs/42/artifacts?per_page=100': {'total_count': 1, 'artifacts': [artifact]},
    }


def reader(evidence):
    def read(path):
        assert path.startswith(reuse.PREFIX)
        return deepcopy(evidence[path.removeprefix(reuse.PREFIX)])
    return read


def test_selects_original_run_attempt_and_artifact_without_credentials(evidence):
    result = reuse.select('42', SHA, read=reader(evidence))
    assert (result['run_id'], result['run_attempt'], result['artifact_id']) == (42, 2, 71)
    assert result['source_revision'] == SHA


@pytest.mark.parametrize('key,value', [
    ('event', 'pull_request'), ('head_branch', 'candidate'), ('head_sha', 'b' * 40),
    ('status', 'in_progress'), ('conclusion', 'failure'), ('workflow_id', 18),
    ('path', '.github/workflows/untrusted.yml'), ('run_attempt', 0),
    ('repository', {'full_name': 'other/crucible', 'id': 11}),
    ('head_repository', {'full_name': 'fork/crucible', 'id': 12}),
    ('repository', None), ('head_repository', []),
])
def test_untrusted_or_incomplete_run_blocks(evidence, key, value):
    evidence['runs/42'][key] = value
    with pytest.raises(ValueError):
        reuse.select('42', SHA, read=reader(evidence))


@pytest.mark.parametrize('key,value', [
    ('expired', True), ('size_in_bytes', 20_000_001), ('size_in_bytes', 0),
    ('name', f'beta-release-{SHA}-1'), ('id', True),
    ('workflow_run', {'id': 43, 'head_branch': 'main', 'head_sha': SHA}),
    ('workflow_run', None),
])
def test_expired_wrong_or_oversized_artifacts_block(evidence, key, value):
    evidence['runs/42/artifacts?per_page=100']['artifacts'][0][key] = value
    with pytest.raises(ValueError):
        reuse.select('42', SHA, read=reader(evidence))


@pytest.mark.parametrize('mutation', ['job_attempt', 'job_failure', 'publish_skipped', 'already_promoted',
                                      'duplicate_job', 'incomplete_jobs', 'duplicate_artifact', 'incomplete_artifacts'])
def test_requires_complete_same_attempt_publish_only_evidence(evidence, mutation):
    jobs = evidence['runs/42/attempts/2/jobs?per_page=100']
    job = jobs['jobs'][0]
    artifacts = evidence['runs/42/artifacts?per_page=100']
    if mutation == 'job_attempt': job['run_attempt'] = 1
    if mutation == 'job_failure': job['conclusion'] = 'failure'
    if mutation == 'publish_skipped': job['steps'][1]['conclusion'] = 'skipped'
    if mutation == 'already_promoted': job['steps'][2]['conclusion'] = 'success'
    if mutation == 'duplicate_job': jobs['jobs'].append(deepcopy(job)); jobs['total_count'] = 2
    if mutation == 'incomplete_jobs': jobs['total_count'] = 101
    if mutation == 'duplicate_artifact': artifacts['artifacts'] *= 2; artifacts['total_count'] = 2
    if mutation == 'incomplete_artifacts': artifacts['total_count'] = 101
    with pytest.raises(ValueError):
        reuse.select('42', SHA, read=reader(evidence))


def test_rerun_during_selection_blocks(evidence):
    read = reader(evidence)
    calls = []
    def changed(path):
        value = read(path)
        if path == reuse.PREFIX + 'runs/42':
            calls.append(path)
            if len(calls) == 2: value['run_attempt'] = 3
        return value
    with pytest.raises(ValueError, match='changed'):
        reuse.select('42', SHA, read=changed)


@pytest.mark.parametrize('run_id', ['', '0', '-1', '42/../43', '42\nnew=value', '1' * 21])
def test_input_cannot_inject_api_paths_or_github_outputs(run_id):
    with pytest.raises(ValueError):
        reuse.select(run_id, SHA, read=lambda path: pytest.fail('No API before validation'))


@pytest.fixture
def downloaded(tmp_path):
    artifact = tmp_path / 'artifact'
    receipts = artifact / 'crucible-release'
    receipts.mkdir(parents=True)
    for name in ('prepared.json', 'verified.json', 'published.json'):
        (receipts / name).write_text(json.dumps({'source_revision': SHA, 'published': name == 'published.json'}))
    (receipts / 'build-metadata.json').write_text('{"containerimage.config.digest":"sha256:fixture"}')
    (artifact / 'release.py').write_text('raise RuntimeError("Downloaded code must never run")')
    return artifact


def test_stages_only_named_data_receipts(evidence, downloaded, tmp_path):
    selection = reuse.select('42', SHA, read=reader(evidence))
    output = tmp_path / 'staged'
    reuse.stage(selection, downloaded, output, read=reader(evidence))
    assert {path.name for path in output.iterdir()} == {
        'prepared.json', 'verified.json', 'published.json', 'build-metadata.json', 'reused-release.json'}
    assert json.loads((output / 'reused-release.json').read_text()) == selection
    with pytest.raises(FileExistsError):
        reuse.stage(selection, downloaded, output, read=reader(evidence))


@pytest.mark.parametrize('mutation', ['artifact_changed', 'previous_promotion', 'wrong_sha',
                                      'missing_receipt', 'symlink', 'duplicate_json'])
def test_stage_refuses_stale_or_ambiguous_receipts_before_writing(evidence, downloaded, tmp_path, mutation):
    selection = reuse.select('42', SHA, read=reader(evidence))
    receipts = downloaded / 'crucible-release'
    published = receipts / 'published.json'
    if mutation == 'artifact_changed': evidence['runs/42/artifacts?per_page=100']['artifacts'][0]['id'] = 72
    if mutation == 'previous_promotion': (receipts / 'promotion.json').write_text('{}')
    if mutation == 'wrong_sha': published.write_text(json.dumps({'source_revision': 'b' * 40, 'published': True}))
    if mutation == 'missing_receipt': published.unlink()
    if mutation == 'symlink':
        published.unlink()
        published.symlink_to(receipts / 'verified.json')
    if mutation == 'duplicate_json': published.write_text('{"published":false,"published":true}')
    output = tmp_path / 'staged'
    with pytest.raises(ValueError):
        reuse.stage(selection, downloaded, output, read=reader(evidence))
    assert not output.exists()


def test_workflows_share_serialization_and_keep_provenance_before_credentials():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / '.github/workflows/promote-beta.yml').read_text()
    build = (root / '.github/workflows/release-beta.yml').read_text()
    assert 'group: crucible-beta-release\n  cancel-in-progress: false' in workflow
    assert 'group: crucible-beta-release\n  cancel-in-progress: false' in build
    assert "if: github.ref == 'refs/heads/main'" in workflow
    assert workflow.index('deploy/source_ci.py') < workflow.index('deploy.reuse_release select')
    assert workflow.index('deploy.reuse_release stage') < workflow.index('deploy/release.py authenticate')
    assert 'artifact-ids: ${{ steps.original.outputs.artifact_id }}' in workflow
    assert '--attempt "$ORIGINAL_ATTEMPT"' in workflow
    assert 'docker build' not in workflow and 'write-all' not in workflow
    assert 'deploy/verify_deployment.py' in workflow and 'deploy/verify_deployment.py' in build
