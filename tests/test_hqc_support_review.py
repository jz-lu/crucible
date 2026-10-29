"""Supplemental claims stay bound to the frozen profiles and codec domain."""
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.hqc_support_review import load_reviewed_profiles, supplemental_evidence, support_bounds

ROOT=Path(__file__).resolve().parents[1]
ROWS=load_reviewed_profiles(ROOT/'crucible/data/hqc_model_profiles.json')


def test_all_profiles_and_stronger_first_four_are_supplemental_only():
    evidence=[supplemental_evidence(r) for r in ROWS]
    assert len(evidence)==66
    assert [r['tier'] for r,e in zip(ROWS,evidence) if e['deterministic_guarantee_established']]==[1,2,3,4]
    assert [e['residual_weight_maximum'] for e in evidence[:4]]==[6,15,28,45]
    assert all(e['decoder_error_capacity']==63 and e['failure_bound']=='0' for e in evidence[:4])
    assert all(not e['model_transfer_required'] for e in evidence[:4])
    assert all(e['model_transfer_required'] for e in evidence[4:])
    assert all(not e['replaces_frozen_certificate'] for e in evidence)


def test_support_threshold_treats_tie_as_potential_failure():
    r=deepcopy(ROWS[0]);r.update(weight=1,encryption_weight=1,error_weight=61)
    assert support_bounds(r)['deterministic_guarantee_established']
    r['error_weight']=62
    assert not support_bounds(r)['deterministic_guarantee_established']


@pytest.mark.parametrize('mutation',['weight','certificate'])
def test_tail_certificate_cannot_be_reused_for_changed_parameters(mutation):
    r=deepcopy(ROWS[4])
    if mutation=='weight':r['weight']+=1
    else:r['correctness_certificate']['certified_model_bound_exponent']+=1
    with pytest.raises(ValueError,match='not bound'):supplemental_evidence(r)


@pytest.mark.parametrize('field,value',[('rs_length',17),('rm_length',64),('rs_delta',True),('code_length',0),('n',1)])
def test_wrong_codec_parameters_fail_closed(field,value):
    with pytest.raises(ValueError):supplemental_evidence({**ROWS[0],field:value})


def test_missing_tail_certificate_is_not_upgraded_to_a_proof():
    r=deepcopy(ROWS[4]);r.pop('correctness_certificate')
    with pytest.raises(ValueError):supplemental_evidence(r)


def test_changed_frozen_table_is_not_covered_by_this_review(tmp_path):
    target=tmp_path/'profiles.json'
    target.write_bytes((ROOT/'crucible/data/hqc_model_profiles.json').read_bytes()+b' ')
    with pytest.raises(ValueError):load_reviewed_profiles(target)
