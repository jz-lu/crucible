"""Reproducible finite witnesses, deliberately distinct from DFR claims."""
from scripts.hqc_module_structure_checks import fwht,hqc_correlation_witness,ring_factors


def test_transform_roundtrip_preserves_exact_counts():
    values=[3,1,0,4,2,8,1,0]
    assert fwht(fwht(values))==[len(values)*v for v in values]


def test_tiny_uniform_support_noise_is_not_radially_symmetric():
    result=hqc_correlation_witness()
    assert (result['n'],result['w'],result['wr'],result['we'])==(11,2,3,3)
    assert result['primitive_prime_ring']
    assert result['denominator']==13588678125
    row=result['same_weight_different_probability'][0]
    assert row=={'weight':3,'low_count':13770163,'low_pattern':'00000001011',
                 'high_count':13792911,'high_pattern':'11100000000'}
    assert 'NOT a deployed-tuple DFR refutation' in result['scope']


def test_current_module_rings_have_a_factorization_boundary():
    rows=ring_factors()
    assert [r['n'] for r in rows]==[8,16,32,64,128,256]
    assert [r['irreducible_factor_degree'] for r in rows]==[1,1,1,1,1,2]
    assert [r['factor_count'] for r in rows]==[8,16,32,64,128,128]
    assert [r['linear_roots'] for r in rows]==[8,16,32,64,128,0]
    assert all(r['message_entropy_bits']==r['n'] for r in rows)
    assert all(int(r['generic_full_message_enumeration_candidates'])==2**r['n'] for r in rows)
