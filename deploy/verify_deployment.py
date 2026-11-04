"""Bounded, anonymous rollout probes. No provider credentials or remote writes."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

TARGETS = ('https://crucible.fail', 'https://crucible-beta-z7znj.ondigitalocean.app')


class ProbeError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def fetch(url, timeout):
    # Fixed origins and GET paths; no cookies, auth, proxy credentials or redirects.
    if not any(url.startswith(origin + '/') for origin in TARGETS):
        raise ProbeError('Unexpected probe origin')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers={'Accept': 'application/json', 'Cache-Control': 'no-cache'})
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ProbeError('response_too_large')
        return response.status, json.loads(raw)


def probe(origin, revision, *, request=fetch, timeout=5):
    try:
        status, value = request(origin + '/health?rollout=' + uuid4().hex, timeout)
        if status != 200:
            return 'health_unavailable'
        if type(value) is not dict or value.get('status') != 'ok':
            return 'health_invalid'
        if value.get('source_revision') != revision:
            return 'revision_pending'
        # AppPlatformIngress permits provider-host readiness, while ordinary
        # routes retain Host and intentionally fail TrustedHostMiddleware there.
        if origin == TARGETS[1]:
            return 'passed'
        status, value = request(origin + '/api/notebook/notes?summary=true', timeout)
        if 200 <= status < 300:
            return 'private_access_failed'
        if status != 401 or type(value) is not dict or value.get('code') != 'authentication_required':
            return 'private_denial_unverified'
        return 'passed'
    except (ValueError, OSError, TimeoutError):
        # Never retain response bodies, exception messages or private note data.
        return 'request_failed'


def verify(revision, *, request=fetch, wait_seconds=600, clock=time.monotonic,
           sleep=time.sleep, save=lambda record: None):
    if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision):
        raise ProbeError('Expected exact released Git SHA')
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 900:
        raise ProbeError('Wait must be between 0 and 900 seconds')
    record = {'format': 'crucible-rollout-v1', 'status': 'waiting', 'source_revision': revision,
              'started_at': datetime.now(timezone.utc).isoformat(), 'rounds': 0,
              'consecutive_passes': 0, 'required_passes': 3, 'observations': {},
              'checks': {TARGETS[0]: ['source_revision', 'anonymous_private_denial'],
                         TARGETS[1]: ['source_revision']},
              'remote_writes_performed': False, 'image_digest_verified': False}
    save(record)
    deadline = clock() + wait_seconds
    while True:
        if clock() >= deadline:
            record['status'] = 'needs_review'
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
            save(record)
            return record
        observations = {}
        for origin in TARGETS:
            remaining = deadline - clock()
            observations[origin] = (probe(origin, revision, request=request, timeout=min(5, remaining))
                                    if remaining > 0 else 'deadline_reached')
        record['rounds'] += 1
        record['observations'] = observations
        record['consecutive_passes'] = (record['consecutive_passes'] + 1
                                        if all(value == 'passed' for value in observations.values()) else 0)
        unsafe = 'private_access_failed' in observations.values()
        if record['consecutive_passes'] >= record['required_passes']:
            record['status'] = 'passed'
        elif unsafe or clock() >= deadline:
            record['status'] = 'needs_review'
        if record['status'] != 'waiting':
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
        save(record)
        if record['status'] != 'waiting':
            return record
        sleep(min(10, max(0, deadline - clock())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--wait-seconds', type=int, default=600)
    args = parser.parse_args()
    # Refuse overwrite so a retry does not erase the previous rollout's outcome.
    with args.output.open('x'):
        pass
    def save(record):
        temporary = args.output.with_suffix('.tmp')
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
        temporary.replace(args.output)
    result = verify(args.revision, wait_seconds=args.wait_seconds, save=save)
    print(json.dumps(result, sort_keys=True))
    if result['status'] != 'passed':
        print('Rollout unverified; inspect receipts and provider status before retrying. No automatic rollback.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(str(error) if isinstance(error, ProbeError) else 'Deployment verification failed', file=sys.stderr)
        raise SystemExit(1)
