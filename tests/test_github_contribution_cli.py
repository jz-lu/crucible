"""Reviewed exports and retry-safe GitHub writes using a synthetic provider.

Crucible reads use the real authorized ASGI handlers; GitHub is an in-memory
Git-object/ref/PR model. No credentials, repositories, or real messages are used.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest

from crucible.github_auth import GitHubIdentity
from scripts import crucible_cli as cli
from test_github_contributions import system, submitted


class Site:
    server = 'http://127.0.0.1:8000'

    def __init__(self, actor):
        self.actor, self.calls, self.expected_handle = actor, [], None

    def request(self, path):
        self.calls.append(path)
        response = self.actor.client.get(path)
        if response.status_code != 200:
            raise cli.ClientError('The connected account cannot export this contribution.')
        return response.json()


class GitHub:
    def __init__(self):
        self.identity = {'id': 12345, 'login': 'synthetic-contributor'}
        self.repo = {'id': 456, 'full_name': 'countableclouds/crucible-schemes',
                     'owner': {'login': 'countableclouds'}, 'private': True, 'archived': False,
                     'fork': False, 'permissions': {'push': True}}
        self.base, self.base_tree = 'a' * 40, 'b' * 40
        self.refs, self.trees, self.commits, self.pulls, self.calls = {}, {}, {}, [], []
        self.lose_reply = None
        self.exists = None
        self.history = 'identical'

    def __call__(self, method, path, body=None, *, allow_status=()):
        self.calls.append((method, path, deepcopy(body)))
        route = unquote(urlsplit(path).path)
        prefix = 'repos/' + self.repo['full_name']
        if method == 'GET':
            if route == 'user': return deepcopy(self.identity)
            if route == prefix: return deepcopy(self.repo)
            if route == prefix + '/pulls': return deepcopy(self.pulls)
            if route.startswith(prefix + '/pulls/') and route.endswith('/files'):
                from urllib.parse import parse_qs
                page = int(parse_qs(urlsplit(path).query)['page'][0])
                return deepcopy(self.pulls[0]['_files'][(page - 1) * 100:page * 100])
            if route == prefix + '/git/ref/heads/main': return {'object': {'sha': self.base}}
            if route.startswith(prefix + '/git/ref/heads/'):
                name = route.split('/git/ref/heads/')[1]
                if name in self.refs: return {'object': {'sha': self.refs[name]}}
                assert 404 in allow_status
                return None
            if route == prefix + '/git/commits/' + self.base:
                return {'sha': self.base, 'tree': {'sha': self.base_tree}}
            if route.startswith(prefix + '/git/commits/'):
                return deepcopy(self.commits[route.rsplit('/', 1)[1]])
            if route.startswith(prefix + '/compare/'): return {'status': self.history}
            if route.startswith(prefix + '/contents/'):
                assert 404 in allow_status
                return self.exists
        if method == 'POST':
            kind = route.rsplit('/', 1)[1]
            if kind == 'trees':
                sha = hashlib.sha1(cli.encode(body)).hexdigest()
                self.trees[sha] = deepcopy(body['tree'])
                result = {'sha': sha}
            elif kind == 'commits':
                sha = hashlib.sha1(cli.encode(body)).hexdigest()
                self.commits[sha] = {'sha': sha, 'tree': {'sha': body['tree']},
                    'parents': [{'sha': parent} for parent in body['parents']], 'message': body['message']}
                result = self.commits[sha]
            elif kind == 'refs':
                assert body['ref'].startswith('refs/heads/crucible/recoveries/')
                name = body['ref'].removeprefix('refs/heads/')
                assert name not in self.refs, 'Never overwrite an existing branch.'
                self.refs[name] = body['sha']
                result = {'object': {'sha': body['sha']}}
            elif kind == 'pulls':
                assert not self.pulls, 'Only one PR may be created for this contribution.'
                assert body['draft'] is True and body['base'] == 'main'
                commit = self.commits[self.refs[body['head']]]
                files = []
                for item in self.trees[commit['tree']['sha']]:
                    raw = item['content'].encode()
                    files.append({'filename': item['path'], 'status': 'added',
                        'sha': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()})
                result = {'number': 7, 'html_url': 'https://github.com/' + self.repo['full_name'] + '/pull/7',
                    'user': self.identity, 'head': {'ref': body['head'], 'repo': {'id': self.repo['id']}},
                    'base': {'ref': 'main', 'repo': {'id': self.repo['id']}},
                    'body': body['body'], 'state': 'open', 'merged_at': None, '_files': files}
                self.pulls.append(result)
            else:
                raise AssertionError((method, path, body))
            if self.lose_reply == kind:
                self.lose_reply = None
                raise cli.ClientError('Synthetic lost reply', 'github_uncertain')
            return deepcopy(result)
        raise AssertionError((method, path, body))


def arguments(path, **changes):
    return SimpleNamespace(path=str(path), repository=None, base=None, without_source=False, without_inputs=False,
                           confirm_publish=True, **changes)


@pytest.fixture
def prepared(system, tmp_path, monkeypatch):
    app, owner, *_ = system
    result, candidate, source = submitted(system)
    with app.state.db.transaction(write=True) as session:
        session.add(GitHubIdentity(github_user_id='12345', user_id=owner.identifier,
            github_login='synthetic-contributor', created_at=system[-1][0], last_login_at=system[-1][0]))
    site = Site(owner)
    root = tmp_path / 'reviewable-contribution'
    args = arguments(root, submission_id=result['id'])
    receipt = cli.github_prepare(site, args)
    provider = GitHub()
    monkeypatch.setattr(cli, 'github_api', provider)
    return site, args, receipt, provider, candidate


def test_prepare_has_no_github_effect_and_only_contains_bound_evidence(prepared):
    site, args, receipt, provider, candidate = prepared
    root, state, files = cli.github_workspace(args.path)
    assert receipt['status'] == 'prepared' and len(files) == 5 and not provider.calls
    assert {item['path'] for item in files} == {'README.md', 'receipt.json', 'provenance.json', 'public-inputs.json', 'source/attack/solve.py'}
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in root.rglob('*') if path.is_file())
    assert root.stat().st_mode & 0o777 == 0o700
    assert candidate not in json.dumps(files)
    assert sum('/challenges/' in path for path in site.calls) == 1
    assert 'must never execute' in next(item['content'] for item in files if item['path'].endswith('.py'))


def test_publish_is_authored_by_linked_account_and_exact_retries_reuse_pr(prepared):
    site, args, _, provider, _ = prepared
    first = cli.github_publish(site, args)
    writes = [call for call in provider.calls if call[0] == 'POST']
    assert first['pull_request_url'].endswith('/pull/7')
    assert len(provider.refs) == len(provider.pulls) == 1
    assert all(item['path'].startswith('contributions/') and item['mode'] == '100644'
               for tree in provider.trees.values() for item in tree)
    assert cli.github_publish(site, args) == first
    assert [call for call in provider.calls if call[0] == 'POST'] == writes
    provider.pulls[0]['state'] = 'closed'; provider.pulls[0]['merged_at'] = 'synthetic-time'
    assert cli.github_publish(site, args)['state'] == 'merged'
    assert len(provider.pulls) == 1


@pytest.mark.parametrize('phase', ['trees', 'commits', 'refs', 'pulls'])
def test_lost_github_write_reply_resumes_without_duplicate_pr(prepared, phase):
    site, args, _, provider, _ = prepared
    provider.lose_reply = phase
    with pytest.raises(cli.ClientError, match='lost reply'):
        cli.github_publish(site, args)
    assert cli.github_publish(site, args)['number'] == 7
    assert len(provider.refs) == len(provider.pulls) == 1


@pytest.mark.parametrize('failure', ['confirmation', 'identity', 'access', 'fork', 'file', 'symlink', 'history', 'existing-path'])
def test_rejected_publication_has_no_visible_github_writes(prepared, failure, tmp_path):
    site, args, _, provider, _ = prepared
    root = Path(args.path)
    if failure == 'confirmation': args.confirm_publish = False
    elif failure == 'identity': provider.identity['id'] = 99999
    elif failure == 'access': provider.repo['permissions']['push'] = False
    elif failure == 'fork': provider.repo['fork'] = True
    elif failure == 'file': (root / 'README.md').write_text('Changed after review')
    elif failure == 'symlink':
        (root / 'README.md').unlink(); (root / 'README.md').symlink_to(tmp_path / 'private')
    elif failure == 'history': provider.history = 'diverged'
    else: provider.exists = [{'name': 'existing evidence'}]
    with pytest.raises(cli.ClientError): cli.github_publish(site, args)
    assert not provider.refs and not provider.pulls


def test_unlinked_account_can_prepare_but_cannot_publish(system, tmp_path, monkeypatch):
    result, *_ = submitted(system, source=False)
    site, provider = Site(system[1]), GitHub()
    monkeypatch.setattr(cli, 'github_api', provider)
    args = arguments(tmp_path / 'unlinked', submission_id=result['id'])
    assert not cli.github_prepare(site, args)['github_linked']
    with pytest.raises(cli.ClientError, match='Link your GitHub'): cli.github_publish(site, args)
    assert not provider.calls


def test_modified_remote_diff_is_not_accepted_as_a_valid_retry(prepared):
    site, args, _, provider, _ = prepared
    cli.github_publish(site, args)
    provider.pulls[0]['_files'][0]['sha'] = 'f' * 40
    with pytest.raises(cli.ClientError, match='exactly the reviewed'): cli.github_publish(site, args)
    assert len(provider.pulls) == 1


def test_modified_retry_tree_cannot_publish_unreviewed_files(prepared):
    site, args, _, provider, _ = prepared
    provider.lose_reply = 'refs'
    with pytest.raises(cli.ClientError): cli.github_publish(site, args)
    path = Path(args.path) / cli.GITHUB_PLAN
    plan = cli.load_json(path); plan['tree_sha'] = 'f' * 40; cli.save_json(path, plan)
    with pytest.raises(cli.ClientError, match='differs from the reviewed'): cli.github_publish(site, args)
    assert not provider.pulls and len(provider.refs) == 1


def test_cli_requires_explicit_publish_confirmation():
    parsed = cli.parser().parse_args(['github', 'publish', '--path', 'reviewed'])
    assert not parsed.confirm_publish
    assert cli.parser().parse_args(['github', 'prepare', str(__import__('uuid').uuid4()), '--path', 'reviewed']).github_action == 'prepare'


def test_receipt_only_preparation_does_not_fetch_source_or_public_inputs(system, tmp_path):
    result, *_ = submitted(system)
    site = Site(system[1])
    args = arguments(tmp_path / 'receipt-only', submission_id=result['id'])
    args.without_source = args.without_inputs = True
    cli.github_prepare(site, args)
    _, _, files = cli.github_workspace(args.path)
    assert site.calls == ['/api/submissions/' + result['id'] + '/github-export']
    assert {item['path'] for item in files} == {'README.md', 'receipt.json', 'provenance.json'}


@pytest.mark.parametrize('part', ['public', 'manifest'])
def test_changed_original_inputs_cannot_enter_prepared_evidence(system, tmp_path, part):
    result, *_ = submitted(system)
    site = Site(system[1])
    request = site.request
    def changed(path):
        value = request(path)
        if path.endswith('/download'):
            if part == 'public': value['challenge']['public']['ciphertext'] = '00'
            else: value['scheme_manifest']['id'] = 'another-configuration'
        return value
    site.request = changed
    args = arguments(tmp_path / 'changed-inputs', submission_id=result['id'])
    with pytest.raises(cli.ClientError, match='do not match'): cli.github_prepare(site, args)
    assert not Path(args.path).exists()
