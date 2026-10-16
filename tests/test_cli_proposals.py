"""Agent proposal workspaces, private review feedback and durable write retries."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crucible.protocol_specs import ProtocolSpec, builtin_spec, canonical_spec, template_spec
from scripts import crucible_cli as cli
from proposal_fixtures import tier_design

TOKEN = 'cru_' + 'a' * 43
SECRET = 'PRIVATE_ERROR_VALUE_DO_NOT_ECHO'


@pytest.fixture
def proposal_server(tmp_path):
    state = {'requests': [], 'records': {}, 'initial': {}, 'history': {}, 'retries': {},
             'reviews': {}, 'writes': [], 'disconnect_once': False, 'forbidden': False,
             'bad_validation_hash': False, 'token': TOKEN, 'owner': 'alice'}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self): self.respond()
        def do_POST(self): self.respond()
        def reply(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
        def validate(self, specification):
            try:
                value = canonical_spec(ProtocolSpec.model_validate(specification))
            except ValidationError as error:
                self.reply({'detail': SECRET + TOKEN, 'errors': [
                    {'path': 'specification.' + '.'.join(map(str, item['loc'])),
                     'message': SECRET + TOKEN, 'input': SECRET} for item in error.errors()]}, 422)
                return None
            return value
        def document(self, identifier, query):
            current = state['records'].get(identifier)
            if current is None:
                return self.reply({'detail': SECRET}, 404)
            before = int(query.get('before_revision', [current['revision']+1])[0])
            limit = int(query.get('limit', ['20'])[0])
            rows = [row for row in reversed(state['history'][identifier]) if row['revision'] < before]
            page = rows[:limit]
            return self.reply({'proposal': current, 'author': {'id': 'owner-id', 'handle': state['owner']},
                'revisions': page, 'reviews': state['reviews'][identifier],
                'next_before_revision': page[-1]['revision'] if len(rows)>limit else None})
        def respond(self):
            parsed = urlsplit(self.path); path = parsed.path; query = parse_qs(parsed.query)
            body = json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.command == 'POST' else None
            state['requests'].append({'path': self.path, 'body': body, 'auth': self.headers.get('Authorization'),
                                      'origin': self.headers.get('Origin')})
            if path == '/api/protocol/spec-template': return self.reply(template_spec())
            if path == '/api/protocol/spec-schema': return self.reply(ProtocolSpec.model_json_schema())
            if path == cli.PROPOSALS + '/validate':
                if self.headers.get('Origin') != state['origin']:
                    return self.reply({'detail': SECRET}, 403)
                value = self.validate(body['specification'])
                if value is None: return
                digest = hashlib.sha256(cli.encode(value)).hexdigest()
                return self.reply({'specification': value, 'content_sha256': '0'*64 if state['bad_validation_hash'] else digest,
                                   'validation': {'status': 'format-valid'}})
            if state['forbidden']:
                return self.reply({'detail': SECRET + TOKEN}, 403)
            if self.headers.get('Authorization') != 'Bearer ' + state['token']:
                return self.reply({'detail': SECRET + TOKEN}, 401)
            if path == '/api/me/protocol-proposals':
                return self.reply({'proposals': list(state['records'].values())})
            if self.command == 'GET' and path.startswith(cli.PROPOSALS + '/'):
                return self.document(path.split('/')[4], query)
            if self.command == 'POST' and (path == cli.PROPOSALS or path.endswith('/revisions')):
                state['writes'].append(deepcopy(body))
                value = self.validate(body['specification'])
                if value is None: return
                content_hash = hashlib.sha256(cli.encode(value)).hexdigest()
                key = (path, body['idempotency_key'])
                if key in state['retries']:
                    old_body, record = state['retries'][key]
                    if old_body != body: return self.reply({'detail': SECRET}, 409)
                else:
                    if path == cli.PROPOSALS:
                        identifier = str(uuid4()); revision = 1
                        state['history'][identifier] = []; state['reviews'][identifier] = []
                    else:
                        identifier = path.split('/')[4]
                        previous = state['records'][identifier]
                        if body['expected_revision'] != previous['revision'] or body['content_sha256'] != previous['content_sha256']:
                            return self.reply({'detail': SECRET}, 409)
                        revision = previous['revision']+1
                    record = {'id': identifier, 'status': 'submitted', 'revision': revision,
                              'created_at': 123456.0, 'content_sha256': content_hash, 'specification': value}
                    state['records'][identifier] = deepcopy(record)
                    state['history'][identifier].append(deepcopy(record))
                    state['retries'][key] = (deepcopy(body), deepcopy(record))
                if state['disconnect_once']:
                    state['disconnect_once'] = False
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                return self.reply({'proposal': record, 'validation': {'status': 'format-valid'}})
            self.reply({'detail': SECRET}, 404)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    state['origin'] = 'http://127.0.0.1:' + str(httpd.server_port)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
    config = tmp_path / 'credentials' / 'config.json'
    cli.save_json(config, {'format': 'crucible-client-config-v1', 'default_server': state['origin'],
        'servers': {state['origin']: {'access_token': TOKEN, 'expires_at': time.time()+3600, 'handle': 'alice'}}})
    yield state, config, tmp_path / 'proposal'
    httpd.shutdown();httpd.server_close();worker.join()


def invoke(server, *args):
    state, config, _ = server
    return cli.main(['--server', state['origin'], '--config', str(config), '--json', 'proposal', *map(str, args)])


def prepared(server):
    _, _, root = server
    assert invoke(server, 'init', '--path', root) == 0
    specification = deepcopy(builtin_spec('teaching-dh-kem', '4'))
    specification['name'] = 'Private candidate KEM'
    specification['summary'] = 'A private proposal fixture, not a mathematical novelty claim.'
    specification['_instructions'] = {'guidance': 'This is stripped from the canonical frozen document.'}
    cli.save_specification(root / 'specification.json', specification)
    return root


def submitted(server):
    root = prepared(server)
    assert invoke(server, 'submit', '--path', root) == 0
    return root, cli.load_json(root / 'proposal-receipt.json')['proposal']


def test_init_and_format_validation_need_no_account_and_execute_no_reference(proposal_server, capsys):
    state, config, root = proposal_server
    config.unlink()
    assert invoke(proposal_server, 'init', '--path', root) == 0
    assert {'specification.json','schema.json','template.json','.crucible-proposal.json','README.md'} <= {p.name for p in root.iterdir()}
    assert invoke(proposal_server, 'validate', '--path', root) == 1
    error = json.loads(capsys.readouterr().out.splitlines()[-1])['error']
    assert error['code'] == 'http_422'
    assert any(item['path'] == 'specification.algorithms.keygen' for item in error['field_errors'])
    assert SECRET not in json.dumps(error) and TOKEN not in json.dumps(error)
    assert all(row['auth'] is None for row in state['requests'])
    assert state['requests'][-1]['origin'] == state['origin']
    assert state['records'] == {} and not (root/'.crucible/proposal-submission.json').exists()
    assert all(TOKEN not in p.read_text() for p in root.rglob('*') if p.is_file())
    assert 'Format' in (root/'template.json').read_text() or '_instructions' in (root/'template.json').read_text()


def test_validation_sanitizes_unknown_property_names_and_server_messages(proposal_server, capsys):
    root = prepared(proposal_server)
    specification = cli.load_json(root/'specification.json')
    specification[SECRET+TOKEN] = SECRET
    cli.save_specification(root/'specification.json', specification)
    capsys.readouterr()
    assert invoke(proposal_server, 'validate', '--path', root) == 1
    output = capsys.readouterr().out
    assert SECRET not in output and TOKEN not in output
    assert '<unknown-field>' in output and 'schema.json' in output


def test_tier_design_errors_keep_useful_paths_without_echoing_values(proposal_server, capsys):
    root = prepared(proposal_server)
    specification = cli.load_json(root/'specification.json')
    specification['tier_design'] = {**tier_design(), 'endpoint_target': ''}
    cli.save_specification(root/'specification.json', specification)
    capsys.readouterr()
    assert invoke(proposal_server, 'validate', '--path', root) == 1
    output = capsys.readouterr().out
    error = json.loads(output)['error']
    assert any(item['path'] == 'specification.tier_design.endpoint_target'
               for item in error['field_errors'])
    assert SECRET not in output and TOKEN not in output


def test_complete_tier_design_roundtrips_in_agent_submission(proposal_server, capsys):
    state, _, _ = proposal_server
    root = prepared(proposal_server)
    specification = cli.load_json(root/'specification.json')
    specification['tier_design'] = tier_design()
    cli.save_specification(root/'specification.json', specification)
    capsys.readouterr()
    assert invoke(proposal_server, 'submit', '--path', root) == 0
    receipt = cli.load_json(root/'proposal-receipt.json')
    pending = cli.load_json(root/'.crucible/proposal-submission.json')
    recorded = state['records'][receipt['proposal']['id']]
    assert recorded['specification']['tier_design'] == tier_design()
    assert pending['payload']['specification']['tier_design'] == tier_design()
    assert 'tier_design' in (root/'README.md').read_text()


def test_validation_checks_hash_without_submitting_or_overwriting_specification(proposal_server, capsys):
    state, _, _ = proposal_server
    root = prepared(proposal_server); before = (root/'specification.json').read_bytes()
    capsys.readouterr()
    assert invoke(proposal_server, 'validate', '--path', root) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'format-valid'
    assert (root/'specification.json').read_bytes() == before and not state['writes']
    state['bad_validation_hash'] = True
    assert invoke(proposal_server, 'submit', '--path', root) == 1
    assert not state['writes'] and not (root/'.crucible/proposal-submission.json').exists()


def test_submit_freezes_canonical_body_and_saves_safe_linked_receipt(proposal_server, capsys):
    state, _, _ = proposal_server
    root = prepared(proposal_server); capsys.readouterr()
    assert invoke(proposal_server, 'submit', '--path', root) == 0
    report = json.loads(capsys.readouterr().out)
    receipt = cli.load_json(root/'proposal-receipt.json')
    pending = cli.load_json(root/'.crucible/proposal-submission.json')
    assert '_instructions' not in pending['payload']['specification']
    assert report['proposal_url'] == state['origin'] + '/#propose?proposal=' + receipt['proposal']['id']
    assert receipt['proposal_url'] == report['proposal_url']
    assert report['proposal']['revision'] == 1 and not report['replayed']
    assert 'specification' not in report['proposal']
    assert pending['owner_handle'] == 'alice'
    assert (root/'.crucible/proposal-submission.json').stat().st_mode & 0o777 == 0o600
    assert all(TOKEN not in p.read_text() for p in root.rglob('*') if p.is_file())
    before = len(state['requests'])
    assert invoke(proposal_server, 'submit', '--path', root) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay['replayed'] and replay['proposal_url'] == report['proposal_url']
    assert len(state['requests']) == before and len(state['records']) == 1


def test_lost_committed_response_reuses_identical_frozen_request(proposal_server, capsys):
    state, _, _ = proposal_server
    root = prepared(proposal_server); state['disconnect_once'] = True; capsys.readouterr()
    assert invoke(proposal_server, 'submit', '--path', root) == 1
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'network_error'
    frozen = (root/'.crucible/proposal-submission.json').read_bytes()
    assert len(state['records']) == 1 and not (root/'proposal-receipt.json').exists()
    assert invoke(proposal_server, 'submit', '--path', root) == 0
    assert len(state['records']) == 1 and state['writes'][0] == state['writes'][1]
    assert cli.load_json(root/'.crucible/proposal-submission.json')['payload'] == json.loads(frozen)['payload']
    assert len([r for r in state['requests'] if r['path'].endswith('/validate')]) == 1


@pytest.mark.parametrize('change', ['specification','account','payload','origin'])
def test_frozen_retry_refuses_changed_content_account_payload_or_origin(proposal_server, change):
    state, config, _ = proposal_server
    root = prepared(proposal_server); state['disconnect_once'] = True
    assert invoke(proposal_server, 'submit', '--path', root) == 1
    if change == 'specification':
        spec = cli.load_json(root/'specification.json');spec['summary']='Changed after write'
        cli.save_specification(root/'specification.json', spec)
    elif change == 'account':
        saved=cli.configuration(config);saved['servers'][state['origin']]['handle']='bob';cli.save_json(config,saved)
    elif change == 'payload':
        path=root/'.crucible/proposal-submission.json';saved=cli.load_json(path)
        saved['payload']['idempotency_key']=str(uuid4());cli.save_json(path,saved)
    else:
        path=root/'.crucible-proposal.json';saved=cli.load_json(path);saved['server']='https://elsewhere.example';cli.save_json(path,saved)
    before=len(state['requests'])
    assert invoke(proposal_server,'submit','--path',root)==1
    assert len(state['requests'])==before and len(state['writes'])==1


def test_same_author_can_reconnect_and_retry_after_token_rotation(proposal_server):
    state, config, _ = proposal_server
    root=prepared(proposal_server);state['disconnect_once']=True
    assert invoke(proposal_server,'submit','--path',root)==1
    new='cru_'+'b'*43;state['token']=new
    saved=cli.configuration(config);saved['servers'][state['origin']]['access_token']=new;cli.save_json(config,saved)
    assert invoke(proposal_server,'submit','--path',root)==0
    assert state['writes'][0]==state['writes'][1] and len(state['records'])==1


def test_revision_workspace_preserves_review_feedback_and_exact_compare_and_swap(proposal_server,capsys):
    state,_,_=proposal_server
    root,record=submitted(proposal_server);identifier=record['id']
    state['records'][identifier]['status']='changes_requested'
    state['reviews'][identifier]=[{'revision':1,'decision':'changes_requested','notes':'Explain the full randomness and error bound.','evidence':'Manual fixture review'}]
    revision=root.parent/'revision-2';capsys.readouterr()
    assert invoke(proposal_server,'revise',identifier,'--path',revision)==0
    metadata=cli.load_json(revision/'.crucible-proposal.json')
    assert metadata['expected_revision']==1 and metadata['content_sha256']==record['content_sha256']
    assert cli.load_json(revision/'feedback.json')['reviews'][0]['notes'].startswith('Explain')
    spec=cli.load_json(revision/'specification.json');spec['summary']='A revised specification with the requested explanation.'
    cli.save_specification(revision/'specification.json',spec)
    assert invoke(proposal_server,'submit','--path',revision)==0
    payload=state['writes'][-1]
    assert payload['expected_revision']==1 and payload['content_sha256']==record['content_sha256']
    assert state['records'][identifier]['revision']==2 and state['records'][identifier]['status']=='submitted'
    capsys.readouterr()
    assert invoke(proposal_server,'show',identifier,'--limit',1)==0
    shown=json.loads(capsys.readouterr().out)
    assert shown['proposal']['revision']==2 and shown['next_before_revision']==2 and shown['reviews']
    assert invoke(proposal_server,'show',identifier,'--before-revision',2)==0
    assert json.loads(capsys.readouterr().out)['revisions'][0]['revision']==1
    assert invoke(proposal_server,'list')==0
    listed=json.loads(capsys.readouterr().out)['proposals']
    assert listed[0]['id']==identifier and listed[0]['revision']==2
    assert 'specification' not in listed[0]


def test_stale_revision_remains_frozen_and_does_not_overwrite_newer_content(proposal_server,capsys):
    state,_,_=proposal_server
    root,record=submitted(proposal_server);identifier=record['id'];revision=root.parent/'revision-2'
    assert invoke(proposal_server,'revise',identifier,'--path',revision)==0
    state['records'][identifier]['revision']=2
    capsys.readouterr()
    assert invoke(proposal_server,'submit','--path',revision)==1
    error=json.loads(capsys.readouterr().out)['error']
    assert error['code']=='http_409' and 'proposal revise' in error['message']
    assert cli.load_json(revision/'.crucible/proposal-submission.json')['payload']['expected_revision']==1
    assert state['records'][identifier]['revision']==2


def test_revision_lost_response_has_one_new_revision(proposal_server):
    state,_,_=proposal_server
    root,record=submitted(proposal_server);identifier=record['id'];revision=root.parent/'revision-2'
    assert invoke(proposal_server,'revise',identifier,'--path',revision)==0
    state['disconnect_once']=True
    assert invoke(proposal_server,'submit','--path',revision)==1
    assert invoke(proposal_server,'submit','--path',revision)==0
    assert len(state['history'][identifier])==2 and state['writes'][-1]==state['writes'][-2]


def test_old_permissions_have_actionable_reconnect_error_without_echoing_server_text(proposal_server,capsys):
    state,_,_=proposal_server
    root=prepared(proposal_server);state['forbidden']=True;capsys.readouterr()
    assert invoke(proposal_server,'submit','--path',root)==1
    error=capsys.readouterr().out
    assert 'login again' in error and SECRET not in error and TOKEN not in error
    assert (root/'.crucible/proposal-submission.json').exists()
    assert not state['records']


@pytest.mark.parametrize('kind',['nonempty','symlink'])
def test_init_refuses_unsafe_or_existing_destination_before_network(proposal_server,kind):
    state,_,root=proposal_server
    if kind=='nonempty':root.mkdir();(root/'owned.txt').write_text('Preserve me')
    else:
        target=root.parent/'target';target.mkdir();root.symlink_to(target,target_is_directory=True)
    assert invoke(proposal_server,'init','--path',root)==1
    assert state['requests']==[]


def test_input_limit_and_server_binding_apply_before_validation_request(proposal_server):
    state,config,_=proposal_server;root=prepared(proposal_server)
    before=len(state['requests'])
    assert cli.main(['--server','https://elsewhere.example','--config',str(config),'--json','proposal','validate','--path',str(root)])==1
    (root/'specification.json').write_text('x'*(cli.MAX_SPECIFICATION+1))
    assert invoke(proposal_server,'validate','--path',root)==1
    assert len(state['requests'])==before


def test_revision_destination_does_not_allow_another_account(proposal_server):
    state,config,_=proposal_server;root,record=submitted(proposal_server)
    saved=cli.configuration(config);saved['servers'][state['origin']]['handle']='bob';cli.save_json(config,saved)
    destination=root.parent/'wrong-account'
    assert invoke(proposal_server,'revise',record['id'],'--path',destination)==1
    assert not destination.exists()


def test_large_valid_document_is_saved_without_expanding_past_local_limit(tmp_path):
    path=tmp_path/'specification.json'
    # The helper preserves compact-valid documents even if pretty printing grows.
    value={'format':'crucible-kem-spec-v1','items':list(range(7000))}
    assert len(cli.encode(value))<cli.MAX_SPECIFICATION
    cli.save_specification(path,value)
    assert cli.load_json(path,cli.MAX_SPECIFICATION)==value
