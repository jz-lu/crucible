"""Lossless module-LWE research KEM: independent, nonstandard construction.

ML-KEM-inspired n=256, rank1 (first tier) / rank3, CBD2 and reencryption transform; variable prime
modulus, coefficient-domain matrix sampling and lossless ciphertexts. This is
NOT ML-KEM's wire format and does not inherit its security claim or theorem.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import selectors
import struct
import subprocess
import sys
import time

SCHEME_ID = "ml-kem"
SCHEME_VERSION = "modulus-research-v1"
N, K, ETA = 256, 3, 2
# Frozen output of the source-pinned calibration; Tier1 is rank1, all others rank3.
MODULI = (170141183460469231731687303715884105727, 9223372036854775837, 141294653018085649, 32708103382879999, 6430126401042769, 1062982610065399, 219409699295707, 48855330487741, 13582856163259, 3776332688993, 1196753035241, 404619960419, 153554212403, 58274179433, 24823457077, 11938265533, 5440936027, 2791019587, 1471642973, 772592699, 447930913, 260454833, 155146727, 98270947, 60891151, 38792489, 25610087, 16913713, 11745221, 8160041, 5591081, 3843451, 2790643, 2084833, 1536251, 1132979, 860051, 661513, 510463, 390647, 309193, 247939, 201437, 159311, 125617, 104147, 85733, 69737, 57329, 47339)
ESTIMATED_BITS = (None, None, 13.14, 15.476, 17.812, 20.44, 22.776, 25.112, 27.448, 30.076, 32.412, 35.04, 37.376, 40.004, 42.34, 44.676, 47.304, 49.64, 51.976, 54.604, 56.94, 59.276, 61.904, 64.24, 66.868, 69.204, 71.551329, 74.17015, 76.504708, 78.843292, 81.35473, 84.120946, 86.437013, 88.769019, 91.288769, 93.763546, 96.074312, 98.697025, 101.047738, 103.422451, 106.004949, 108.333777, 110.668353, 113.350378, 115.734375, 117.988862, 120.589512, 123.022911, 125.30526, 127.978777)
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
GENERATION_TIMEOUT_SECONDS = 20.0
SIZE_PARAMETER = {"name": "Challenge tier", "symbol": "", "unit": "tier",
    "meaning": "Index into a frozen modulus ladder for this research construction",
    "mapping": "Tier1 has rank1; tiers2-50 rank3. Larger moduli make fixed, full CBD2 noise relatively smaller; later tiers decrease q. Not ML-KEM parameter sets.",
    "comparison_scope": "scheme-version"}
INITIAL_SECURITY_MODEL = {"id": "module-lwe-core-svp-v1", "name": "Module-LWE research classical Core-SVP reference",
    "status": "heuristic-reference", "tier_range": {"min": 1, "max": 50},
    "scope": "Classical primal uSVP and dual hybrid costs for flattened LWE n768,m768, CBD2 secret and error; ADPS16 Core-SVP/GSA. Not a complete module/KEM security claim.",
    "estimator_commit": "53da5982597709ba0fdf94ea37a84d822310fd84",
    "limitations": ["Flattened LWE heuristic omits additional module structure and the full ciphertext's partial message information.",
        "No complete security reduction, side-channel, quantum or multi-target analysis claimed.",
        "The minimum beta40 floor does not support a numerical security claim for the first tiny-cost profile."],
    "references": [{"title": "Pinned lattice-estimator", "url": "https://github.com/malb/lattice-estimator/tree/53da5982597709ba0fdf94ea37a84d822310fd84"}]}


class GenerationLimit(ValueError):
    pass


class GenerationUnavailable(RuntimeError):
    pass


def _q(q):
    if type(q) is not int or q not in MODULI:
        raise ValueError("Unregistered module-LWE research modulus")
    return q


def _rank(q):
    _q(q)
    return 1 if q == MODULI[0] else 3


def _profile(profile_id):
    profiles = {f"module-lwe-v1-tier-{i+1}": (i+1, q) for i,q in enumerate(MODULI)}
    if type(profile_id) is not str or profile_id not in profiles:
        raise ValueError("Unknown module-LWE research profile")
    return profiles[profile_id]


def _bytes(value, size, label):
    if type(value) is not bytes or len(value) != size:
        raise ValueError(f"{label} must contain exactly {size} bytes")
    return value


def cbd(data):
    """Exactly ML-KEM's CBD eta2: four independent bits per coefficient."""
    _bytes(data, 128, "CBD input")
    out = []
    for byte in data:
        for nibble in (byte & 15, byte >> 4):
            out.append((nibble & 1)+((nibble >> 1)&1)-((nibble >> 2)&1)-((nibble >> 3)&1))
    return out


def noise(seed, nonce):
    _bytes(seed, 32, "Noise seed")
    if type(nonce) is not int or not 0 <= nonce <= 255:
        raise ValueError("Invalid noise nonce")
    return cbd(hashlib.shake_256(seed+bytes([nonce])).digest(128))


def uniform_poly(seed, q, row, column):
    """Unbiased coefficient-domain sampling with canonical bitmask/rejection.

    Matrix sampling differs from ML-KEM's NTT-domain 12-bit sampler. Inputs
    bind q to prevent accidental cross-profile seed/matrix interpretation.
    """
    k = _rank(q)
    _q(q); _bytes(seed,32,"Matrix seed")
    if type(row) is not int or type(column) is not int or not 0<=row<k or not 0<=column<k:
        raise ValueError("Invalid matrix index")
    width=(q.bit_length()+7)//8; mask=(1<<q.bit_length())-1
    domain=b"Crucible-Module-LWE-v1/A"+q.to_bytes(16,"little")+seed+bytes([row,column])
    length=width*N*2;offset=0;out=[]
    while len(out)<N:
        # hashlib.digest(length) returns a prefix; extending never reuses words.
        data=hashlib.shake_128(domain).digest(length)
        while offset+width<=len(data) and len(out)<N:
            value=int.from_bytes(data[offset:offset+width],"little")&mask;offset+=width
            if value<q:out.append(value)
        if len(out)<N:
            length*=2
            if length>width*N*32:
                raise GenerationLimit("Matrix rejection sampler exceeded its bounded expansion")
    return out

def poly_mul(a,b,q):
    """Negacyclic product modulo x^256+1 and q; integer schoolbook reference."""
    if len(a)!=N or len(b)!=N:
        raise ValueError("Polynomial degree mismatch")
    out=[0]*(2*N-1)
    for i,x in enumerate(a):
        if x:
            for j,y in enumerate(b):out[i+j]+=x*y
    return [(out[i]-(out[i+N] if i+N<len(out) else 0))%q for i in range(N)]


def _sum_products(left,right,error,q):
    out=list(error)
    for a,b in zip(left,right):
        product=poly_mul(a,b,q)
        out=[(x+y)%q for x,y in zip(out,product)]
    return out


def pack(polys,q):
    width=(q.bit_length()+7)//8
    out=bytearray()
    for poly in polys:
        if len(poly)!=N:raise ValueError("Polynomial degree mismatch")
        for value in poly:
            if type(value) is not int or not 0<=value<q:raise ValueError("Noncanonical coefficient")
            out.extend(value.to_bytes(width,"little"))
    return bytes(out)


def unpack(data,count,q):
    width=(q.bit_length()+7)//8
    _bytes(data,count*N*width,"Packed polynomials")
    values=[int.from_bytes(data[i:i+width],"little") for i in range(0,len(data),width)]
    if any(v>=q for v in values):raise ValueError("Noncanonical coefficient")
    return [values[i*N:(i+1)*N] for i in range(count)]


def _sizes(q):
    k = _rank(q)
    width=(q.bit_length()+7)//8
    return 52+k*N*width,(k+1)*N*width

def _decode_pk(pk,q):
    k = _rank(q)
    size,_=_sizes(q);_bytes(pk,size,"Public key")
    if pk[:4]!=b"MLW1" or pk[4:20]!=q.to_bytes(16,"little"):
        raise ValueError("Public key belongs to another research profile")
    return pk[20:52],unpack(pk[52:],k,q)


@dataclass(frozen=True,repr=False)
class PrivateKey:
    value: bytes

    def __repr__(self):return "PrivateKey(<redacted>)"

def keygen_modulus(q,randomness=None):
    k = _rank(q)
    _q(q)
    coins=os.urandom(64) if randomness is None else _bytes(randomness,64,"Key-generation randomness")
    expanded=hashlib.sha3_512(coins[:32]+bytes([k])).digest()
    rho,sigma=expanded[:32],expanded[32:]
    s=[noise(sigma,i) for i in range(k)];e=[noise(sigma,k+i) for i in range(k)]
    t=[_sum_products([uniform_poly(rho,q,i,j) for j in range(k)],s,e[i],q) for i in range(k)]
    pk=b"MLW1"+q.to_bytes(16,"little")+rho+pack(t,q)
    secret=struct.pack("b"*(k*N),*(x for p in s for x in p))
    return pk,PrivateKey(coins[32:]+secret+pk+hashlib.sha3_256(pk).digest())

def _encrypt(q,pk,message,coins):
    k = _rank(q)
    rho,t=_decode_pk(pk,q);_bytes(message,32,"Message");_bytes(coins,32,"Encryption coins")
    sp=[noise(coins,i) for i in range(k)];ep=[noise(coins,k+i) for i in range(k)];epp=noise(coins,2*k)
    u=[_sum_products([uniform_poly(rho,q,j,i) for j in range(k)],sp,ep[i],q) for i in range(k)]
    bits=int.from_bytes(message,"little")
    v=_sum_products(t,sp,[epp[i]+(((bits>>i)&1)*((q+1)//2)) for i in range(N)],q)
    return pack(u+[v],q)

def encapsulate_modulus(q,public_key,randomness=None):
    _q(q);_decode_pk(public_key,q)
    message=os.urandom(32) if randomness is None else _bytes(randomness,32,"Encapsulation message")
    derived=hashlib.sha3_512(message+hashlib.sha3_256(public_key).digest()).digest()
    return _encrypt(q,public_key,message,derived[32:]),derived[:32]


def decapsulate_modulus(q,private_key,ciphertext):
    k = _rank(q)
    _q(q);pk_size,ct_size=_sizes(q)
    if not isinstance(private_key,PrivateKey):raise ValueError("Invalid private key")
    sk=_bytes(private_key.value,64+k*N+pk_size,"Private key")
    ct=_bytes(ciphertext,ct_size,"Ciphertext")
    pk=sk[32+k*N:-32];_decode_pk(pk,q)
    if not hmac.compare_digest(sk[-32:],hashlib.sha3_256(pk).digest()):raise ValueError("Private-key hash mismatch")
    secret=list(struct.unpack("b"*(k*N),sk[32:32+k*N]))
    if any(abs(x)>ETA for x in secret):raise ValueError("Private coefficient outside CBD2 support")
    s=[secret[i*N:(i+1)*N] for i in range(k)]
    try:
        polys=unpack(ct,k+1,q)
    except ValueError:
        # Length-correct but noncanonical ciphertexts are implicit rejections.
        return hashlib.shake_256(sk[:32]+ct).digest(32)
    product=_sum_products(s,polys[:k],[0]*N,q)
    values=[(x-y)%q for x,y in zip(polys[k],product)]
    message=sum((((2*x+q//2)//q)&1)<<i for i,x in enumerate(values)).to_bytes(32,"little")
    derived=hashlib.sha3_512(message+sk[-32:]).digest()
    expected=_encrypt(q,pk,message,derived[32:])
    return derived[:32] if hmac.compare_digest(ct,expected) else hashlib.shake_256(sk[:32]+ct).digest(32)

def keygen(profile_id):return keygen_modulus(_profile(profile_id)[1])
def encapsulate(profile_id,public_key):return encapsulate_modulus(_profile(profile_id)[1],public_key)
def decapsulate(profile_id,private_key,ciphertext):return decapsulate_modulus(_profile(profile_id)[1],private_key,ciphertext)


def artifact_manifest():
    result={"format":"crucible-implementation-artifact-v1","scheme_id":SCHEME_ID,"scheme_version":SCHEME_VERSION,
        "adapter_source_sha256":IMPLEMENTATION_SHA256,"python_version":platform.python_version(),
        "runtime":"Python standard-library SHA3/SHAKE and OS urandom","side_channel_claim":"None; variable-time isolated research generator",
        "construction":"Lossless coefficient-domain rank1/rank3 module-LWE research KEM; not ML-KEM wire format"}
    result['artifact_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result


def verify_artifact():
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=IMPLEMENTATION_SHA256:
        raise GenerationUnavailable("Module-LWE research source changed after registration")
    return artifact_manifest()


def _worker(q):
    verify_artifact();pk_size,ct_size=_sizes(q);expected=5+pk_size+ct_size+64
    proc=subprocess.Popen([sys.executable,'-I',str(Path(__file__).resolve()),'--generate',str(q)],
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env={'PATH':'/usr/bin:/bin'})
    selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ)
    os.set_blocking(proc.stdout.fileno(),False);output=bytearray();deadline=time.monotonic()+GENERATION_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining=deadline-time.monotonic()
            if remaining<=0:raise GenerationLimit('Module-LWE generation exceeded its process deadline')
            for key,_ in selector.select(remaining):
                data=os.read(key.fileobj.fileno(),min(65536,expected+1-len(output)))
                if not data:selector.unregister(key.fileobj)
                else:
                    output.extend(data)
                    if len(output)>expected:raise GenerationUnavailable('Module-LWE worker exceeded output bound')
        proc.wait(timeout=max(.001,deadline-time.monotonic()))
        if proc.returncode or len(output)!=expected or output[:4]!=b'MLW1' or output[4] not in (0,1):
            raise GenerationUnavailable('Module-LWE worker returned an invalid result')
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise GenerationLimit('Module-LWE generation exceeded its process deadline') from None
    finally:
        selector.close()
        if proc.poll() is None:proc.kill()
        proc.wait();proc.stdout.close()


def _encoding(q):
    pk,ct=_sizes(q)
    return {'public_key':'MLW1 || uint128le(q) || rho32 || rank coefficient polynomials',
        'public_key_bytes':pk,'ciphertext':'rank u polynomials || one v polynomial; fixed-width little-endian coefficients, no compression',
        'ciphertext_bytes':ct,'shared_secret':'32 bytes, 64 hexadecimal characters','shared_secret_bytes':32,
        'coefficient_bytes':(q.bit_length()+7)//8,'polynomial_coefficients':256}


def _kdf():
    return {'name':'Research reencryption transform with SHA3/SHAKE',
        'framing':'K||coins=SHA3-512(mu32||SHA3-256(pk)); return full K32 after deterministic encryption',
        'implicit_rejection':'Failed reencryption or noncanonical ciphertext: SHAKE256(private_z32||ct,32)'}


@dataclass(frozen=True,repr=False)
class GeneratedChallenge:
    public:dict
    target:bytes
    diagnostics:dict
    def __repr__(self):return 'GeneratedChallenge(public=<public inputs>, target=<redacted>, diagnostics=<private>)'


def generate(profile_id):
    tier,q=_profile(profile_id);data=_worker(q);pk_size,ct_size=_sizes(q)
    pk=data[5:5+pk_size];ct=data[5+pk_size:5+pk_size+ct_size];target=data[-64:-32]
    if bool(data[4])!=hmac.compare_digest(target,data[-32:]):raise GenerationUnavailable('Inconsistent worker agreement status')
    public={'scheme_id':SCHEME_ID,'version':SCHEME_VERSION,'profile_id':profile_id,'profile_label':f'Tier {tier}',
        'profile_kind':'research-tier','instance_size':tier,'parameter_bits':q.bit_length(),
        'parameters':{'n':N,'rank':_rank(q),'q':str(q),'eta1':2,'eta2':2,'compression':'none','message_bytes':32},
        'public_key':pk.hex(),'ciphertext':ct.hex(),'encoding':_encoding(q),'kdf':_kdf(),
        'implementation_sha256':IMPLEMENTATION_SHA256,'artifact_sha256':artifact_manifest()['artifact_sha256']}
    return GeneratedChallenge(public,target,{'correctness_checked':True,'shared_secret_matches':bool(data[4]),
        'honest_trials':1,'implementation_sha256':IMPLEMENTATION_SHA256})


def parse_shared_secret(value,profile_id):
    _profile(profile_id)
    if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdefABCDEF' for c in value):
        raise ValueError('Recovered key must contain exactly 64 hexadecimal characters')
    return bytes.fromhex(value)


def get_manifest(profile_id):
    tier,q=_profile(profile_id)
    result={'id':profile_id,'profile_id':profile_id,'scheme_id':SCHEME_ID,'version':SCHEME_VERSION,
        'name':f'Module-lattice research — Tier {tier}','profile_label':f'Tier {tier}','profile_kind':'research-tier',
        'profile_order':tier,'instance_size':tier,'parameter_bits':q.bit_length(),'parameter_label':'modulus bits',
        'size_parameter':dict(SIZE_PARAMETER),'educational':False,'record_inheritance':False,
        'shared_secret_bytes':32,'encoding':_encoding(q),'kdf':_kdf(),
        'parameters':{'n':N,'rank':_rank(q),'q':str(q),'eta1':2,'eta2':2,'compression':'none','message_bytes':32},
        'security_claim':'Experimental, lossless module-LWE KEM inspired by ML-KEM primitives. Different modulus, coefficient-domain matrix sampler, public-key encoding and lossless ciphertexts: not standardized ML-KEM or an inherited security theorem.',
        'assumption_context':'Rank1 at Tier1 and rank3 elsewhere; module-LWE over Z_q[x]/(x^256+1), CBD2 noise, with the stated research reencryption transform and hash assumptions.',
        'distribution':'Full256-bit key-generation seed, private rejection seed and encapsulation message. Exact independent-bit CBD2 sampling and unbiased field coefficient rejection sampling from SHAKE. One fresh honest trial; no correctness-conditioned resampling.',
        'algorithms':{'keygen':'Sample uniform coefficient-domain A and full CBD2 s,e; t=A*s+e.',
            'encapsulate':'Derive K and coins from full mu||H(pk); sample CBD2 r,e1,e2; u=A^T*r+e1, v=t^T*r+e2+Encode(mu); serialize without lossy compression.',
            'decapsulate':'Decode v-s^T*u; reconstruct entire ciphertext from recovered mu, compare, and select K or private SHAKE rejection key.'},
        'correctness':{'kind':'perfect','failure_bound':'0',
            'argument':'Each coefficient of e^T*r-s^T*e1+e2 has magnitude <=2*k*n*eta^2+eta<=6146; every registered prime has floor(q/4)>6146. No ciphertext compression error exists. Thus all256 message bits decode exactly for every allowed noise draw.',
            'scope':'Every registered modulus and all correctly sampled honest keys/encapsulations; mathematical correctness, not implementation safety or confidentiality.',
            'assumptions':'Faithful modular negacyclic arithmetic, bounded CBD2 coefficients, exact encoding and complete reencryption; no independence assumption is needed for the support bound.',
            'evidence_status':'Research implementation tests and exact support-bound argument; no independent cryptographic audit or original ML-KEM security theorem claimed.',
            'implementation_checks':'tests/test_lattice_module.py; every issued instance retains and checks the original encapsulated secret once.'},
        'admission':{'status':'local-beta','official_ranked':False,'reason':'Nonstandard research family; independent scientific review remains separate.'},
        'implementation':{'source':'crucible/lattice_module.py','source_sha256':IMPLEMENTATION_SHA256,
            'artifact_sha256':artifact_manifest()['artifact_sha256'],'version':SCHEME_VERSION,
            'runtime':'Variable-time Python with hard process deadline and bounded output; no decapsulation API',
            'evidence':'docs/lattice-module.txt'},
        'references':[{'title':'FIPS203: source primitives, not this derivative specification','url':'https://csrc.nist.gov/pubs/fips/203/final'},
            {'title':'Pinned lattice-estimator','url':'https://github.com/malb/lattice-estimator/tree/53da5982597709ba0fdf94ea37a84d822310fd84'}]}
    if ESTIMATED_BITS[tier-1] is not None:
        result['initial_security_estimate']={'bits':ESTIMATED_BITS[tier-1],'target_bits':round(8+120*(tier-1)/49,6),
            'model_id':INITIAL_SECURITY_MODEL['id']}
    return result


def manifests():return [get_manifest(f'module-lwe-v1-tier-{i+1}') for i in range(len(MODULI))]
def source_bytes():return Path(__file__).read_bytes()


CORRECTNESS_SPEC = get_manifest('module-lwe-v1-tier-1')['correctness']


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1]!='--generate':raise SystemExit(2)
    q=_q(int(sys.argv[2]));pk,sk=keygen_modulus(q);ct,target=encapsulate_modulus(q,pk);got=decapsulate_modulus(q,sk,ct)
    sys.stdout.buffer.write(b'MLW1'+bytes([hmac.compare_digest(target,got)])+pk+ct+target+got)
