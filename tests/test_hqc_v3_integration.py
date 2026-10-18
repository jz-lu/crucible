"""Exercise the actual current HQC worker, public download and receipt pipeline."""
from uuid import uuid4

from crucible import registry
from scripts.hqc_model_solve_tier1 import solve
from test_api import Actor
from test_calibrated_api import current_system


def test_current_hqc_native_download_solver_submission_and_archive_boundary(current_system):
    app, _, _ = current_system
    actor = Actor(app)
    actor.register('hqc_v3_integration')
    adapter = registry.adapter_for('hqc')
    assert adapter.SCHEME_VERSION == 'modeled-research-v3'
    profile_id = adapter.profile_for_size(1)
    response = actor.post('/api/challenges', {'scheme_id':'hqc', 'profile_id':profile_id})
    assert response.status_code == 200, response.text
    challenge = response.json()['challenge']
    package = actor.client.get(challenge['download_url']).json()
    assert package['scheme_manifest'] == adapter.get_manifest(profile_id)
    public = package['challenge']['public']
    candidate = solve(public).hex()
    method = actor.post('/api/methods', {'name':'Public support enumeration',
        'pseudocode':'Enumerate the weight-one key support from the public syndrome; decode the residual RM symbols; derive the declared KEM output.'}).json()['method']
    receipt = actor.post(f"/api/challenges/{challenge['id']}/submit", {
        'candidate':candidate, 'method_id':method['id'], 'idempotency_key':str(uuid4()),
        'compute':{'hardware':'Local test CPU', 'workers':1, 'wall_seconds':0.1,
                   'precomputation':'None', 'peak_memory':'Not measured', 'notes':'Isolated native integration test'}})
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()['submission']['status'] == 'correct'
    assert candidate not in receipt.text
    person = actor.client.get('/api/people/hqc_v3_integration').json()['person']
    assert person['points'] == 35  # Frozen v9 first weight 7: progress +4x first.
    scheme = next(s for s in actor.client.get('/api/schemes').json()['schemes'] if s['id']=='hqc')
    assert scheme['stage_range'] == {'min':1,'max':66,'step':1}
    assert scheme['first_recovery']['handle'] == 'hqc_v3_integration'
    assert scheme['recovered_profile_ids'] == [profile_id]
    assert len(scheme['profiles']) == 66 and all(p['initial_security_estimate'] is None for p in scheme['profiles'])
    assert actor.client.get(scheme['protocol']['example_url']).content.startswith(b'#!/usr/bin/env python3')
    old = next(r for r in scheme['previous_releases'] if r['version']=='growth-research-v2')
    assert len(old['profiles']) == 27 and old['recovered_profile_ids'] == []
    archived = actor.post('/api/challenges', {'scheme_id':'hqc','scheme_version':old['version'],
        'profile_id':old['profiles'][0]['id']})
    assert archived.status_code == 409
    outcomes = actor.client.get(scheme['protocol']['correctness_url']).json()['outcomes']
    assert outcomes == [{'profile_id':profile_id,'outcome':'agreement','disposition':'issued','count':1}]
