"""Versioned HQC research ladder with conditional modeled correctness.

Ends at the exact 2025 HQC-1 public construction. It does not inherit the latest
2026 security assessment or certify the surrogate-model transfer premise.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct

from . import hqc_model_profiles
from .pq_native import _execute, GeneratedChallenge, GenerationLimit, Unavailable

GenerationUnavailable = Unavailable
SCHEME_ID = 'hqc'
SCHEME_VERSION = 'modeled-research-v3'
ROOT = Path(__file__).resolve().parents[1]
COMMIT = '5a1a854b0dc9f2141bdc771c555ee60c37950183'
ARCHIVE_SHA256 = '83c6e6cff490638312e1e20ed5de593fb5d7bfab162235f6238f519d6a1feafb'
SIZE_PARAMETER = {'name': 'Challenge tier', 'meaning': 'A frozen HQC research parameter tuple',
    'mapping': 'Weights w=1..66 and wr=we=ceil(75w/66), following the 2025 HQC-1 balance. For each pair choose the smallest supported communication footprint meeting the conditional 2^-128 correctness target; the last tuple is exact 2025 HQC-1. Tier does not quantify security.',
    'comparison_scope': 'scheme-version'}
BUILD_FILES = ('crucible/hqc_model_profiles.py', 'crucible/hqc_model_correctness.py',
    'crucible/data/hqc_model_profiles.json', 'crucible/hqc_growth_parameters.py',
    'scripts/hqc_model_parameters.py', 'scripts/build_hqc_model.py', 'scripts/hqc_model_worker.c')
RUNTIME_FILES = (*BUILD_FILES, 'crucible/code_hqc_v3.py', 'crucible/pq_native.py')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


IMPLEMENTATION_SHA256 = sha(__file__)
_IMPORTED_FILES = {name: sha(ROOT/name) for name in RUNTIME_FILES}
PROFILES = tuple(hqc_model_profiles.read_profiles())
_artifact = None


def profile_for_size(size):
    if type(size) is not int or not 1 <= size <= len(PROFILES):
        raise ValueError('Challenge tier must be an integer from 1 to 66')
    return f'{SCHEME_ID}-{SCHEME_VERSION}-tier-{size}'


def profile(profile_id):
    if type(profile_id) is not str:
        raise ValueError('Unknown HQC modeled tier')
    for row in PROFILES:
        if profile_for_size(row['tier']) == profile_id:
            return row
    raise ValueError('Unknown HQC modeled tier')


def directory():
    return Path(os.environ.get('CRUCIBLE_HQC_MODEL_BACKEND', str(ROOT/'.local/native-hqc-model/install'))).resolve()


def artifact_manifest():
    global _artifact
    if _artifact is None:
        try:
            raw = (directory()/'build-manifest.json').read_bytes()
            if len(raw) > 262144:
                raise ValueError()
            build = json.loads(raw)
            if build['format'] != 'crucible-hqc-model-build-v1' or build['source_commit'] != COMMIT or build['source_archive_sha256'] != ARCHIVE_SHA256:
                raise ValueError()
            if any(sha(ROOT/name) != digest for name, digest in _IMPORTED_FILES.items()):
                raise ValueError()
            if build['files'] != {name: _IMPORTED_FILES[name] for name in BUILD_FILES}:
                raise ValueError()
            workers = build['workers']
            if set(workers) != {f"hqc-{row['tier']}" for row in PROFILES}:
                raise ValueError()
            for row in PROFILES:
                name = f"hqc-{row['tier']}"
                if workers[name]['parameters'] != row or sha(directory()/name) != workers[name]['binary_sha256']:
                    raise ValueError()
            closure = {'format': 'crucible-hqc-model-artifact-v1', 'scheme_id': SCHEME_ID,
                'version': SCHEME_VERSION, 'files': dict(_IMPORTED_FILES),
                'native_build_manifest_sha256': hashlib.sha256(raw).hexdigest(),
                'source_commit': COMMIT, 'source_archive_sha256': ARCHIVE_SHA256,
                'source_url': build['source_url'], 'workers': workers,
                'base_static_library_sha256': build['static_library_sha256'],
                'base_build_manifest_sha256': build['base_build_manifest_sha256'],
                'compiler': build['compiler'], 'flags': build['flags']}
            closure['artifact_sha256'] = hashlib.sha256(canonical(closure)).hexdigest()
            _artifact = closure
        except (OSError, KeyError, ValueError, TypeError):
            raise Unavailable('HQC modeled native build missing or inconsistent') from None
    return deepcopy(_artifact)


def verify_artifact(profile_id=None):
    artifact = artifact_manifest()
    try:
        if any(sha(ROOT/name) != digest for name, digest in artifact['files'].items()):
            raise ValueError()
        if sha(directory()/'build-manifest.json') != artifact['native_build_manifest_sha256']:
            raise ValueError()
        names = artifact['workers'] if profile_id is None else [f"hqc-{profile(profile_id)['tier']}"]
        for name in names:
            if sha(directory()/name) != artifact['workers'][name]['binary_sha256']:
                raise ValueError()
    except (OSError, ValueError):
        raise Unavailable('HQC modeled native artifact changed') from None
    return artifact


def parameters(profile_id):
    row = profile(profile_id)
    return {name: row[name] for name in ('n', 'rs_length', 'rs_dimension', 'rs_delta',
        'rm_length', 'rm_repetitions', 'code_length')} | {
        'secret_weight': row['weight'], 'encryption_weight': row['encryption_weight'],
        'error_weight': row['error_weight']}


def encoding(profile_id):
    row = profile(profile_id)
    vector_bytes = (row['n']+7)//8
    return {'public_key': '32-byte seed followed by the native n-bit syndrome; lowercase hex in JSON',
        'public_key_bytes': 32+vector_bytes,
        'ciphertext': 'Native n-bit u, concatenated-code-length v, 16-byte salt; lowercase hex in JSON',
        'ciphertext_bytes': vector_bytes+row['code_length']//8+16,
        'shared_secret': '64 hexadecimal characters, the complete 32-byte KEM output', 'shared_secret_bytes': 32}


def get_manifest(profile_id):
    row = profile(profile_id); tier = row['tier']; cert = row['correctness_certificate']
    endpoint = tier == len(PROFILES)
    return {'id': profile_id, 'profile_id': profile_id, 'scheme_id': SCHEME_ID, 'version': SCHEME_VERSION,
        'name': f'HQC modeled research · Tier {tier}', 'profile_kind': 'research-tier',
        'profile_label': f'Tier {tier}', 'instance_size': tier, 'parameter_bits': row['n'],
        'parameter_label': 'ring length', 'size_parameter': dict(SIZE_PARAMETER),
        'parameters': parameters(profile_id), 'shared_secret_bytes': 32, 'encoding': encoding(profile_id),
        'record_inheritance': False, 'educational': False,
        'kdf': {'construction': 'Retained native HQC 2025-08-22 salted KEM transform'},
        'security_claim': ('Exact 2025 HQC-1 parameter and wire-format endpoint, historically submitted for NIST category 1. The October 2026 specification raises weights to compensate recent cryptanalysis; current endpoint security calibration is pending. ' if endpoint else 'Reduced research tuple. ') + 'No numerical security bound, monotone attack-cost or inherited IND-CCA guarantee is asserted. Full shared-secret recovery tests this exact public distribution.',
        'initial_security_estimate': None,
        'published_reference': {'title': 'HQC-1, specification 2025-08-22',
            'exact_endpoint': endpoint, 'historical_nist_category': 1,
            'current_security_calibration': 'pending; 2026 weights and attack analyses differ'},
        'assumption_context': 'Hamming quasi-cyclic decoding for the exact declared weight distribution, primitive ring and retained salted KEM transform',
        'distribution': 'Full 128-bit message, 128-bit salt, 256-bit seeds and 32-byte output. Uniform key supports in the ideal XOF model and the retained slightly biased encapsulation sampler. No reduced seed entropy or agreement-conditioned resampling.',
        'algorithms': {'keygen': 'Pinned 2025 HQC native key generation with the frozen ring and secret weight',
            'encapsulate': 'Pinned 2025 HQC native encryption with the frozen RS/RM code and encryption/error weights',
            'decapsulate': 'Retained nearest-RM/RS decoder, re-encryption and implicit rejection'},
        'correctness': {'kind': 'statistical', 'failure_bound': '<=2^-128 under the declared conditional model',
            'failure_bound_exponent': 128, 'policy_id': 'crucible-honest-agreement-128-v1',
            'argument': 'Exact rational evaluation of the 2026 HQC Model 1 improved RM bound, RS tail, deterministic-tie coupling correction and native sampler-density factor. The per-profile arithmetic certificate proves the stated bound inside the model; its conservativity for actual correlated ring products is a conjectural premise.',
            'scope': 'Average honest Encaps/Decaps disagreement over uniform messages and keys for this exact tuple, conditional on the stated model and faithful implementation. Resource aborts and implementation faults are separate.',
            'assumptions': 'Model 1 is a surrogate with independent Bernoulli error coordinates at the exact one-coordinate marginal. Actual ring products have correlations and fixed parity; we conjecture the complete adjusted model bound conservatively bounds the actual decoder failure for this tuple. IdealXOF sampling/seed expansion and faithful 2025 native arithmetic, decoding and encodings are also required. This bridge is not proved for reduced tuples.',
            'evidence_status': 'Conditional exact arithmetic; not an unconditional proof or empirical verification of a 2^-128 event rate',
            'model_id': cert['model_id'], 'model_bound_exponent': cert['certified_model_bound_exponent'],
            'model_log2_bound_display': cert['log2_bound_display'],
            'implementation_checks': 'Unconditioned honest checks, native boundary fixtures and exact published-endpoint conformance; see docs/hqc-model-v3.txt'},
        'cost_tradeoff': {'growing_parameters': 'Secret/encryption weights and the selected minimal supported code/ring',
            'cost': 'Public-key and ciphertext bytes grow in natural steps; sampled timing curves may be nonmonotone. Multiple nearby weights can share an encoding size.',
            'selection_scope': 'Minimum public-key+ciphertext bytes at each fixed weight pair over 45 supported codecs, each with the smallest primitive prime above code length, under the conditional correctness rule. Not a full security/efficiency Pareto frontier.'},
        'admission': {'status': 'local-beta research; independent admission pending', 'official_ranked': False,
            'reason': 'Per-configuration conditional correctness, with explicit surrogate-model and current-security gaps'},
        'implementation': {'source': 'crucible/code_hqc_v3.py', 'source_sha256': IMPLEMENTATION_SHA256,
            'artifact_sha256': artifact_manifest()['artifact_sha256'], 'version': SCHEME_VERSION,
            'runtime': 'Pinned parameter-specialized native worker with bounded process resources',
            'evidence': 'docs/hqc-model-v3.txt'},
        'references': [{'title': 'HQC 2025-08-22 construction and parameter anchor', 'url': 'https://pqc-hqc.org/doc/hqc_specifications_2025_08_22.pdf'},
            {'title': 'HQC 2026-10-12 current correctness models and security changes', 'url': 'https://pqc-hqc.org/doc/hqc_specifications_2026_10_12.pdf'},
            {'title': 'Current MAGE-Stern cryptanalysis', 'url': 'https://eprint.iacr.org/2026/1498'},
            {'title': 'Pinned liboqs source release', 'url': 'https://github.com/open-quantum-safe/liboqs/tree/'+COMMIT}]}


def manifests():
    return [get_manifest(profile_for_size(row['tier'])) for row in PROFILES]


def generate(profile_id):
    row = profile(profile_id); artifact = verify_artifact(profile_id); enc = encoding(profile_id)
    output = _execute(directory()/f"hqc-{row['tier']}", 'generate')
    pk, ct = enc['public_key_bytes'], enc['ciphertext_bytes']
    if len(output) != 17+pk+ct+32 or output[:4] != b'CPQ1' or struct.unpack('>III', output[4:16]) != (pk, ct, 32) or output[16] not in (0, 1):
        raise Unavailable('Invalid HQC modeled worker response')
    verify_artifact(profile_id)
    public = {'scheme_id': SCHEME_ID, 'version': SCHEME_VERSION, 'profile_id': profile_id,
        'profile_kind': 'research-tier', 'profile_label': f"Tier {row['tier']}", 'record_inheritance': False,
        'instance_size': row['tier'], 'parameter_bits': row['n'], 'parameters': parameters(profile_id),
        'public_key': output[17:17+pk].hex(), 'ciphertext': output[17+pk:17+pk+ct].hex(),
        'encoding': enc, 'implementation_sha256': IMPLEMENTATION_SHA256,
        'artifact_sha256': artifact['artifact_sha256']}
    return GeneratedChallenge(public, output[-32:], {'correctness_checked': True,
        'shared_secret_matches': bool(output[16]), 'honest_trials': 1,
        'implementation_sha256': IMPLEMENTATION_SHA256, 'artifact_sha256': artifact['artifact_sha256']})


def source_bytes():
    return Path(__file__).read_bytes()
