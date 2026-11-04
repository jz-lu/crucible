"""Select a successful main publish-only run; stage its receipts, never its code.

Run as python -m deploy.reuse_release. GitHub supplies provenance; release.py
still verifies the immutable registry manifest, tested config and current main.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from deploy import release, source_ci

WORKFLOW = '.github/workflows/release-beta.yml'
PREFIX = 'repos/' + source_ci.REPOSITORY + '/actions/'
require = source_ci.require


def select(run_id, revision, *, read=source_ci.api):
    require(isinstance(run_id, str) and re.fullmatch('[1-9][0-9]{0,19}', run_id), 'Expected a release run ID')
    require(isinstance(revision, str) and re.fullmatch('[0-9a-f]{40}', revision), 'Expected exact main SHA')
    workflow = read(PREFIX + 'workflows/release-beta.yml')
    require(source_ci.positive(workflow.get('id')) and workflow.get('path') == WORKFLOW and
            workflow.get('state') == 'active', 'Release workflow is unavailable')
    run = read(PREFIX + 'runs/' + run_id)
    require(run.get('id') == int(run_id) and source_ci.positive(run.get('run_attempt')) and
            run.get('workflow_id') == workflow['id'] and run.get('path') == WORKFLOW and
            run.get('event') == 'workflow_dispatch' and run.get('head_branch') == 'main' and
            run.get('head_sha') == revision and run.get('status') == 'completed' and
            run.get('conclusion') == 'success' and
            type(run.get('repository')) is dict and type(run.get('head_repository')) is dict and
            run['repository'].get('full_name') == source_ci.REPOSITORY and
            run['head_repository'].get('full_name') == source_ci.REPOSITORY,
            'Use a successful Release beta run from this exact main commit')
    attempt = run['run_attempt']
    jobs = read(PREFIX + f'runs/{run_id}/attempts/{attempt}/jobs?per_page=100')
    rows = jobs.get('jobs')
    require(type(rows) is list and type(jobs.get('total_count')) is int and
            jobs['total_count'] == len(rows) <= 100 and all(type(row) is dict for row in rows),
            'Incomplete release job evidence')
    native = [row for row in rows if row.get('name') == 'Build and verify native beta image']
    require(len(native) == 1, 'Missing or duplicate native release job')
    job = native[0]
    require(job.get('run_id') == int(run_id) and job.get('run_attempt') == attempt and
            job.get('head_sha') == revision and job.get('status') == 'completed' and
            job.get('conclusion') == 'success', 'Native release job has not passed in this attempt')
    steps = job.get('steps')
    require(type(steps) is list and all(type(step) is dict for step in steps), 'Missing release steps')
    for name, conclusion in (
        ('Verify exact native catalogue and application regressions', 'success'),
        ('Publish the tested image', 'success'),
        ('Promote the exact tested image to beta', 'skipped'),
    ):
        found = [step for step in steps if step.get('name') == name]
        require(len(found) == 1 and found[0].get('status') == 'completed' and
                found[0].get('conclusion') == conclusion,
                'Use a verified publish-only run; inspect any previous promotion separately')
    inventory = read(PREFIX + f'runs/{run_id}/artifacts?per_page=100')
    rows = inventory.get('artifacts')
    require(type(rows) is list and type(inventory.get('total_count')) is int and
            inventory['total_count'] == len(rows) <= 100 and all(type(row) is dict for row in rows),
            'Incomplete release artifact inventory')
    name = f'beta-release-{revision}-{attempt}'
    matches = [row for row in rows if row.get('name') == name]
    require(len(matches) == 1, 'Exact release artifact is missing or ambiguous')
    artifact = matches[0]
    origin = artifact.get('workflow_run', {})
    require(type(origin) is dict and source_ci.positive(artifact.get('id')) and artifact.get('expired') is False and
            type(artifact.get('size_in_bytes')) is int and 0 < artifact['size_in_bytes'] <= 20_000_000 and
            origin.get('id') == int(run_id) and origin.get('head_sha') == revision and
            origin.get('head_branch') == 'main' and
            origin.get('repository_id') == run['repository'].get('id') and
            origin.get('head_repository_id') == run['head_repository'].get('id') and
            source_ci.positive(origin.get('repository_id')) and
            source_ci.positive(origin.get('head_repository_id')),
            'Release artifact is expired, oversized or belongs to another source')
    current = read(PREFIX + 'runs/' + run_id)
    require(all(current.get(key) == run.get(key) for key in
                ('id', 'run_attempt', 'head_sha', 'status', 'conclusion', 'workflow_id', 'path')),
            'Release run changed during verification; inspect before retrying')
    return {'format': 'crucible-reused-release-v1', 'repository': source_ci.REPOSITORY,
            'source_revision': revision, 'run_id': int(run_id), 'run_attempt': attempt,
            'artifact_id': artifact['id'], 'artifact_name': name,
            'url': f'https://github.com/{source_ci.REPOSITORY}/actions/runs/{run_id}'}


def read_receipt(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 1_000_000,
            'Missing or oversized release receipt')
    def unique(pairs):
        value = dict(pairs)
        require(len(value) == len(pairs), 'Duplicate receipt field')
        return value
    value = json.loads(path.read_bytes(), object_pairs_hook=unique)
    require(type(value) is dict, 'Invalid release receipt')
    return value


def stage(selection, artifact, output, *, read=source_ci.api):
    current = select(str(selection['run_id']), selection['source_revision'], read=read)
    require(current == selection, 'Release evidence changed after selection')
    root = artifact.resolve()
    receipts = root / 'crucible-release'
    require(receipts.resolve() == receipts and not (receipts / 'promotion.json').exists(),
            'Unexpected artifact layout or previous promotion; inspect before retrying')
    values = {name: read_receipt(receipts / name) for name in
              ('prepared.json', 'verified.json', 'published.json', 'build-metadata.json')}
    require(all(values[name].get('source_revision') == selection['source_revision'] for name in
                ('prepared.json', 'verified.json', 'published.json')), 'Receipt source differs from trusted run')
    require(values['verified.json'].get('published') is False and
            values['published.json'].get('published') is True, 'Successful publication required')
    output.mkdir()  # Fresh directory only; never overwrite uncertain promotion evidence.
    for name, value in values.items():
        release.write_json(output / name, value)
    release.write_json(output / 'reused-release.json', selection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['select', 'stage'])
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--selection', required=True, type=Path)
    parser.add_argument('--artifact', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.command == 'stage' and (args.artifact is None or args.output is None):
        parser.error('stage requires --artifact and --output')
    require(os.environ.get('GITHUB_REPOSITORY') == source_ci.REPOSITORY and
            os.environ.get('GITHUB_REF') == 'refs/heads/main' and
            os.environ.get('GITHUB_SHA') == args.revision and
            release.git(release.ROOT, 'rev-parse', 'HEAD') == args.revision,
            'Exact main promotion context required')
    if args.command == 'select':
        result = select(args.run_id, args.revision)
        with args.selection.open('x') as target:
            json.dump(result, target, indent=2, sort_keys=True)
        with open(os.environ['GITHUB_OUTPUT'], 'a') as target:
            for key in ('run_id', 'run_attempt', 'artifact_id'):
                target.write(f'{key}={result[key]}\n')
    else:
        selection = read_receipt(args.selection)
        require(str(selection['run_id']) == args.run_id and selection['source_revision'] == args.revision,
                'Selection differs from requested run')
        stage(selection, args.artifact, args.output)
    print('Verified original publish-only release evidence')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, source_ci.GateError) else 'Release evidence verification failed', file=sys.stderr)
        raise SystemExit(1)
