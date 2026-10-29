#!/usr/bin/env python3
"""Explicit local feedback profiles; not a complete test-selection algorithm."""
import argparse
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
NODE_VERSION = 'v22.23.4'
# Put the fastest/closest cross-feature guard first. Add profiles deliberately;
# a green profile never means that unaffected tests or native checks were run.
PROFILES = {
    'browse': [('node', 'tests/' + name) for name in (
        'test_read_navigation_frontend.cjs',
        'test_resource_cache_frontend.cjs',
        'test_resource_metrics_frontend.cjs',
        'test_browse_warmup_frontend.cjs',
        'test_scheme_navigation_frontend.cjs',
        'test_session_bootstrap_frontend.cjs',
        'test_api_response_frontend.cjs',
        'test_browsing_read_recovery_frontend.cjs',
    )],
    'release': [('python', '-B', '-m', 'pytest', '-q',
                 'tests/test_source_ci.py', 'tests/test_release_pipeline.py',
                 'tests/test_dev_preflight.py')],
}
BOUNDARY = 'Focused preflight only; required CI, browser, native and hosted checks still apply.'


def run(profile, node='node'):
    commands = PROFILES[profile]
    # Validate the complete profile before executing any of it.
    files = [part for command in commands for part in command if part.startswith('tests/')]
    if not files or any(not (ROOT / name).is_file() for name in files):
        print('Preflight references a missing test; update the explicit profile.', file=sys.stderr)
        return 1
    if any(command[0] == 'node' for command in commands):
        version = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=10)
        if version.returncode or version.stdout.strip() != NODE_VERSION:
            print('Use Node ' + NODE_VERSION + ', matching CI; select it with --node /path/to/node.', file=sys.stderr)
            return 1
    print(BOUNDARY, flush=True)
    started = time.monotonic()
    for command in commands:
        program = node if command[0] == 'node' else sys.executable
        print('\nPreflight: ' + ' '.join(command), flush=True)
        result = subprocess.run([program, *command[1:]], cwd=ROOT, timeout=300)
        if result.returncode:
            print('Preflight stopped at the first failure.', file=sys.stderr)
            return 1
    print('\n' + profile + ' preflight passed in ' + str(round(time.monotonic() - started, 1)) + 's. ' + BOUNDARY)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile', nargs='?', default='browse', choices=tuple(PROFILES))
    parser.add_argument('--node', default='node', help='Node executable, matching the pinned CI version')
    parser.add_argument('--list', action='store_true', help='Print every explicit profile without executing tests')
    args = parser.parse_args()
    if args.list:
        for name, commands in PROFILES.items():
            print(name + ':')
            for command in commands:
                print('  ' + ' '.join(command))
        print(BOUNDARY)
        return 0
    return run(args.profile, args.node)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError) as error:
        print('Preflight could not complete: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
