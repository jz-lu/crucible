#!/usr/bin/env python3
"""Read-only, exact-commit main CI gate for deliberate beta releases.

No PR checks, older successes, local receipts or arbitrary workflow names can
satisfy this gate. Candidate branches continue to run the reusable CI workflow.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'jz-lu/crucible'
WORKFLOW = '.github/workflows/tests.yml'
EXPECTED_JOBS = frozenset({
    'Portable source checks (Python 3.11)',
    'Portable source checks (Python 3.13)',
    'Portable source checks (Python 3.14)',
    'Standalone frontend regressions',
    'Critical research and account workflows',
    'Reviewed runtime source contract',
    'Real Chromium UI and private-access checks',
    'SQLite and PostgreSQL 17 metadata-query parity',
    'CI passed',
})


class GateError(ValueError):
    """Fixed operator-readable text, without raw API/token output."""


def require(condition, message):
    if not condition:
        raise GateError(message)


def positive(value):
    return type(value) is int and value > 0


def api(path):
    # Paths are constructed internally. No supplied URL or redirect can receive
    # the job's read-only token. gh keeps credentials out of process arguments.
    require(path.startswith('repos/' + REPOSITORY + '/actions/'), 'Unexpected API path')
    result = subprocess.run(
        ['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
         '-H', 'Accept: application/vnd.github+json',
         '-H', 'X-GitHub-Api-Version: 2022-11-28', path],
        capture_output=True, timeout=30, check=False)
    require(result.returncode == 0, 'Cannot read trusted CI; check Actions read permission and retry')
    require(len(result.stdout) <= 2_000_000, 'Unexpected CI response size')
    try:
        value = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise GateError('Invalid CI response') from None
    require(type(value) is dict, 'Invalid CI response')
    return value


def validate_identity(run, revision, workflow_id):
    require(type(run) is dict and positive(run.get('id')) and positive(run.get('run_attempt')),
            'Missing CI run identity')
    require(positive(run.get('workflow_id')) and run.get('workflow_id') == workflow_id and run.get('path') == WORKFLOW and
            run.get('event') == 'push' and run.get('head_branch') == 'main' and
            run.get('head_sha') == revision and
            isinstance(run.get('repository'), dict) and
            run['repository'].get('full_name') == REPOSITORY and
            isinstance(run.get('head_repository'), dict) and
            run['head_repository'].get('full_name') == REPOSITORY,
            'CI is not the trusted main push workflow for this exact commit')


def validate_success(run, jobs, revision, workflow_id):
    validate_identity(run, revision, workflow_id)
    require(run.get('status') == 'completed' and run.get('conclusion') == 'success',
            'Exact main CI has not passed; resolve or rerun that CI before releasing')
    require(type(jobs) is dict and type(jobs.get('jobs')) is list and
            type(jobs.get('total_count')) is int and
            jobs['total_count'] == len(jobs['jobs']) == len(EXPECTED_JOBS),
            'Missing or additional CI jobs; review the source gate inventory')
    rows = jobs['jobs']
    require(all(type(row) is dict and positive(row.get('id')) for row in rows) and
            len({row['id'] for row in rows}) == len(rows) and
            {row.get('name') for row in rows} == EXPECTED_JOBS,
            'Missing, duplicate or unexpected CI gates')
    require(all(positive(row.get('run_id')) and positive(row.get('run_attempt')) and
                row.get('run_id') == run['id'] and row.get('run_attempt') == run['run_attempt'] and
                row.get('head_sha') == revision and row.get('status') == 'completed' and
                row.get('conclusion') == 'success' for row in rows),
            'Every expected gate and aggregate must pass in this exact run attempt')


def latest_run(revision, read):
    value = read('repos/' + REPOSITORY + '/actions/workflows/tests.yml/runs?'
                 'branch=main&event=push&head_sha=' + revision + '&per_page=100')
    runs = value.get('workflow_runs')
    require(type(runs) is list and type(value.get('total_count')) is int and
            0 <= value['total_count'] == len(runs) <= 100,
            'Incomplete CI run inventory')
    require(all(type(run) is dict and positive(run.get('id')) for run in runs),
            'Invalid CI run inventory')
    # A later failure/pending rerun must never fall back to an older success.
    return max(runs, key=lambda run: run['id']) if runs else None


def verify(revision, *, read=api, wait_seconds=900, clock=time.monotonic, sleep=time.sleep):
    require(isinstance(revision, str) and re.fullmatch('[0-9a-f]{40}', revision), 'Expected exact Git SHA')
    workflow = read('repos/' + REPOSITORY + '/actions/workflows/tests.yml')
    require(positive(workflow.get('id')) and workflow.get('path') == WORKFLOW and
            workflow.get('state') == 'active', 'Trusted CI workflow is unavailable')
    workflow_id = workflow['id']
    deadline = clock() + wait_seconds
    while True:
        run = latest_run(revision, read)
        if run is not None:
            validate_identity(run, revision, workflow_id)
            if run.get('status') == 'completed':
                require(run.get('conclusion') == 'success',
                        'Latest exact main CI failed; resolve or rerun that CI before releasing')
                attempt_path = ('repos/' + REPOSITORY + '/actions/runs/' + str(run['id']) +
                                '/attempts/' + str(run['run_attempt']) + '/jobs?per_page=100')
                jobs = read(attempt_path)
                validate_success(run, jobs, revision, workflow_id)
                current = latest_run(revision, read)
                require(current is not None, 'CI run disappeared during verification')
                validate_identity(current, revision, workflow_id)
                require((current['id'], current['run_attempt'], current.get('status'), current.get('conclusion')) ==
                        (run['id'], run['run_attempt'], 'completed', 'success'),
                        'CI changed during verification; retry after the latest run completes')
                return {'format': 'crucible-source-ci-v1', 'status': 'passed', 'repository': REPOSITORY,
                        'source_revision': revision, 'workflow': WORKFLOW, 'workflow_id': workflow_id,
                        'run_id': run['id'], 'run_attempt': run['run_attempt'],
                        'url': 'https://github.com/' + REPOSITORY + '/actions/runs/' + str(run['id']),
                        'gates': sorted(EXPECTED_JOBS),
                        'verified_at': datetime.now(timezone.utc).isoformat()}
            require(run.get('status') in ('queued', 'in_progress', 'waiting', 'pending', 'requested'),
                    'Unexpected CI run state')
        require(clock() < deadline, 'Timed out waiting for exact main CI; inspect that run before retrying')
        sleep(min(15, max(0, deadline - clock())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY and
            os.environ.get('GITHUB_REF') == 'refs/heads/main' and
            os.environ.get('GITHUB_SHA') == args.revision, 'Exact main release context required')
    head = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], timeout=10).decode().strip()
    require(head == args.revision, 'Checkout differs from requested source CI')
    require(not args.output.exists(), 'Use a fresh source CI receipt path')
    result = verify(args.revision)
    result['workflow_sha256'] = hashlib.sha256((ROOT / WORKFLOW).read_bytes()).hexdigest()
    with args.output.open('x') as target:
        json.dump(result, target, indent=2, sort_keys=True)
        target.write('\n')
    print('Verified source CI: ' + result['url'] + ' (attempt ' + str(result['run_attempt']) + ')')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, GateError) else 'Source CI verification failed', file=sys.stderr)
        raise SystemExit(1)
