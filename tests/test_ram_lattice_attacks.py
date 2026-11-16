"""Checks of the mathematical attack contract, including actual public recovery."""
from fractions import Fraction
from itertools import product
from math import comb

import pytest

from crucible.attack_models import lattice
from crucible import module_compressed_v4_core as module
from crucible import lattice_frodo_v3 as frodo
from crucible import ntru_reference


def profile(scheme,n):
    if scheme=="ml-kem":
        return {"profile_id":f"module-test-{n}","parameters":{"n":n,"rank":2,"q":3329,"eta1":3,"eta2":2,"du":10,"dv":4}}
    return {"profile_id":f"frodo-test-{n}","parameters":{"n":n,"q":32768,"nbar":8}}


def test_first_match_lemma_exhaustive_small_maps():
    # All maps from three native inputs to three ciphertexts, and all decoders.
    # G(x)=x makes every mistaken native input a mistaken key (worst case).
    size=3
    for ciphertexts in product(range(size),repeat=size):
        first={}
        for message,ciphertext in enumerate(ciphertexts):
            first.setdefault(ciphertext,message)
        successes=sum(first[ciphertexts[m]]==m for m in range(size))
        for decoder in product(range(size),repeat=size):
            bad=sum(decoder[ciphertexts[m]]!=m for m in range(size))
            assert Fraction(successes,size)>=lattice.first_match_success_lower_bound(Fraction(bad,size))


def test_complete_module_search_recovers_real_shared_key_without_private_input():
    p=module.Parameters(8,2,3)
    pk,_sk=module.keygen_internal(p,b"d"*32,b"z"*32)
    target_ct,target_key=module.encapsulate_internal(p,pk,b"\xb7")
    matches=[]
    for value in range(256):
        ct,key=module.encapsulate_internal(p,pk,bytes([value]))
        if ct==target_ct:
            matches.append((value,key))
    assert matches==[(0xb7,target_key)]


def test_module_curves_have_actual_bit_cost_and_only_relevant_premises():
    rows=[lattice.estimate("ml-kem","compressed-research-v4",profile("ml-kem",n)) for n in (8,16,32,64,128,256)]
    assert all(row["work_bound"]==sum(row["breakdown"].values()) for row in rows)
    assert all(row["work_bound"]>row["candidate_count"] for row in rows)
    assert [row["candidate_count"] for row in rows]==[1<<n for n in (8,16,32,64,128,256)]
    assert rows[0]["assumptions"]==rows[1]["assumptions"]==[]
    assert rows[0]["success_lower_bound"]=="1"
    assert all(Fraction(row["success_lower_bound"])>=Fraction(1,2) for row in rows)
    assert "rounding" not in rows[2]["assumptions"][0]["human"]
    assert "rounding" in rows[4]["assumptions"][0]["human"]


def test_module_parameters_are_not_silently_reinterpreted():
    wrong=profile("ml-kem",32)
    wrong["parameters"]["rank"]=3
    with pytest.raises(ValueError):
        lattice.estimate("ml-kem","compressed-research-v4",wrong)
    with pytest.raises(ValueError):
        lattice.estimate("ml-kem","packed-research-v3",profile("ml-kem",32))


def test_frodo_native_search_holds_the_observed_public_salt_fixed():
    # This verifies the exact attack candidate path, not a completed2^128 search.
    pk,_sk=frodo.keygen_dimension(8,b"f"*64)
    message=b"m"*16
    salt=b"s"*32
    ct,key=frodo.encapsulate_dimension(8,pk,message+salt)
    reencryption,k=frodo._encrypt(8,pk,message,ct[-32:])
    assert reencryption==ct
    assert frodo._shake(ct+k,16)==key
    other,_=frodo._encrypt(8,pk,message,b"t"*32)
    assert other!=ct


def test_frodo_column_probability_bound_by_counting_one_row():
    # Over all residues in each possible gcd subgroup and all25-element windows,
    # the bound holds. Shifts have period16; these cover each congruence class.
    q=32768
    for v in range(5):
        spacing=1<<v
        for shift in range(16):
            hits=sum((value-shift)%spacing==0 for value in range(25))
            assert Fraction(hits,q//spacing)<=Fraction(1,1024)
    assert lattice.frodo_false_secret_bound(8)<Fraction(1,1<<39)


def test_bounded_column_solver_and_allowance():
    # A diagonal public matrix with widely spaced outputs has one bounded
    # solution; recover it solely from public A,b. This is a mechanics fixture.
    a=[[100,0],[0,100]]
    secret=(1,-1)
    b=[101,32767-100]
    assert lattice.recover_bounded_lwe_column(a,b,bound=1)==secret
    with pytest.raises(ValueError,match="allowance"):
        lattice.recover_bounded_lwe_column(a,b,max_candidates=3)


def test_frodo_envelope_changes_for_a_mathematical_reason():
    low=lattice.estimates_for("frodokem","anchor-research-v3",profile("frodokem",8))
    high=lattice.estimates_for("frodokem","anchor-research-v3",profile("frodokem",640))
    assert low[1]["work_bound"]<low[0]["work_bound"]
    assert high[0]["work_bound"]<high[1]["work_bound"]
    assert low[0]["candidate_count"]==high[0]["candidate_count"]==1<<128
    assert low[1]["assumptions"][0]["id"]=="frodo-v3-independent-public-matrix"
    assert low[0]["assumptions"]==[]
    assert all(Fraction(row["success_lower_bound"])>Fraction(1,2) for row in low+high)


def test_all_frodo_profiles_have_defined_monotone_baseline_envelope():
    rows=[lattice.estimate("frodokem","anchor-research-v3",profile("frodokem",n)) for n in range(8,641,8)]
    assert all(a["work_bound"]<b["work_bound"] for a,b in zip(rows,rows[1:]))
    assert all(row["memory_bits"]>0 for row in rows)
    assert {row["algorithm"] for row in rows}=={"frodo-native-message-enumeration-v1","frodo-bounded-secret-column-enumeration-v1"}


def test_ntru_count_and_complete_reference_candidate_path():
    p,q,w=7,67,2
    pk=ntru_reference.encode([1,2,3,4,5,6,7],[q]*p)
    r=[1,-1,0,0,0,0,0]
    ct,key=ntru_reference.hide(p,q,w,pk,r)
    # The public-only search is tested on a selected message of a tiny arithmetic
    # fixture; the fixture is not represented as a registered/admitted key.
    assert ntru_reference.recover_low_tier({"parameters":{"p":p,"q":q,"w":w},"public_key":pk.hex(),"ciphertext":ct.hex()})==key
    with pytest.raises(ValueError):
        lattice.estimate("sntrup","3",{"id":"not-registered","parameters":{"p":p,"q":q,"w":w}})
    row=lattice.estimate("sntrup","3",{"id":"sntrup-v3-tier-1","parameters":{"p":5,"q":79,"w":3}})
    assert row["candidate_count"]==comb(5,3)*2**3
    assert row["assumptions"]==[]
    assert row["success_lower_bound"]=="1"
    assert row["work_bound"]==sum(row["breakdown"].values())
    assert row["work_bound"]>row["candidate_count"]
