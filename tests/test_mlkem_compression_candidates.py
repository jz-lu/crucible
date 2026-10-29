"""Exact arithmetic regression, strict decoding boundary and useful candidates."""
import json
from pathlib import Path

import pytest
from scripts.mlkem_compression_candidates import candidate, compression_counts, distributions

ROOT=Path(__file__).resolve().parents[1]
OLD=json.loads((ROOT/'docs/mlkem-correctness-candidates.json').read_text())


@pytest.mark.parametrize('n',[8,16,32,64,128,256])
def test_standard_widths_reproduce_existing_certificate_exponents(n):
    row=next(r for r in OLD['configurations'] if r['candidate_anchor']=='ML-KEM-512' and r['rank']==2 and r['n']==n)
    for e in row['evidence']:
        got=candidate(n,10,4,e['mode'],exact=True)
        assert got['meets_target']==e['meets_target']
        if 'certified_dyadic_bits' in e:assert got['certified_exponent']==e['certified_dyadic_bits']


@pytest.mark.parametrize('n,du,dv,expected_ct,exponent',[(8,8,3,19,None),(16,9,2,40,158),(32,9,3,84,176),(64,10,3,184,141),(128,11,3,400,131)])
def test_conservative_candidates_keep_noise_and_full_message_degree(n,du,dv,expected_ct,exponent):
    r=candidate(n,du,dv,'conservative-compression',exact=True)
    assert r['meets_target']
    assert r['ciphertext_bytes']==expected_ct
    assert (r['n'],r['rank'],r['q'],r['eta1'],r['eta2'])==(n,2,3329,3,2)
    if exponent is None:assert r['argument']=='deterministic-support'
    else:assert r['certified_exponent']==exponent


@pytest.mark.parametrize('bits',range(1,13))
def test_compression_counts_against_independent_integer_formulas(bits):
    expected={}
    for value in range(3329):
        encoded=(((value*(1<<bits)+1664)//3329)%(1<<bits))
        decoded=(3329*encoded+(1<<(bits-1)))>>bits
        error=(decoded-value+1664)%3329-1664
        expected[error]=expected.get(error,0)+1
    assert compression_counts(bits)==dict(sorted(expected.items()))


def test_small_rounding_budget_does_not_imply_failure_for_standard_n128():
    assert not candidate(128,10,4,'conservative-compression',exact=True)['meets_target']
    # Failure of this analytical bound is not proof of a real correctness failure.
    assert candidate(128,10,4,'independent-uniform-u-compression',exact=True)['meets_target']


@pytest.mark.parametrize('n,du,dv,mode',[(True,10,4,'conservative-compression'),(7,10,4,'conservative-compression'),(32,0,4,'conservative-compression'),(32,13,4,'conservative-compression'),(32,10,True,'conservative-compression'),(32,10,4,'typo')])
def test_unsupported_parameters_and_unknown_model_are_rejected(n,du,dv,mode):
    with pytest.raises(ValueError):candidate(n,du,dv,mode)


def test_distribution_cannot_silently_convert_an_unknown_model():
    with pytest.raises(ValueError):distributions(10,'conservative-typo')
