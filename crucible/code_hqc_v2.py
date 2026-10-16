"""HQC-derived growth ladder with a fixed zero honest-decryption failure bound.

This is an isolated research release. Legacy HQC artifacts and receipts retain
exactly their previous identities. No standard-profile security claim transfers.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct

from . import hqc_growth_parameters
from .pq_native import _execute, GeneratedChallenge, GenerationLimit, Unavailable

GenerationUnavailable = Unavailable
SCHEME_ID = 'hqc'
SCHEME_VERSION = 'growth-research-v2'
ROOT = Path(__file__).resolve().parents[1]
COMMIT = '5a1a854b0dc9f2141bdc771c555ee60c37950183'
ARCHIVE_SHA256 = '83c6e6cff490638312e1e20ed5de593fb5d7bfab162235f6238f519d6a1feafb'
SIZE_PARAMETER = {'name': 'Challenge tier', 'meaning': 'A frozen HQC research parameter tuple',
    'mapping': 'Weight w=wr=we increases from 1 to 27. Each tier selects the smallest supported concatenated code meeting the same perfect-correctness bound and the smallest admissible primitive ring prime above that code length. Costs rise in discrete steps; no linear or monotone attack-cost estimate is asserted.',
    'comparison_scope': 'scheme-version'}
BUILD_FILES = ('crucible/hqc_growth_parameters.py', 'crucible/data/hqc_growth_profiles.json',
    'scripts/hqc_growth_parameters.py', 'scripts/build_hqc_growth.py', 'scripts/hqc_growth_worker.c')
RUNTIME_FILES = (*BUILD_FILES, 'crucible/code_hqc_v2.py', 'crucible/pq_native.py')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


IMPLEMENTATION_SHA256 = sha(__file__)
_IMPORTED_FILES = {name: sha(ROOT/name) for name in RUNTIME_FILES}
PROFILES = tuple(hqc_growth_parameters.read_profiles())
_artifact = None


def profile_for_size(size):
    if type(size) is not int or not 1 <= size <= len(PROFILES):
        raise ValueError('Challenge tier must be an integer from 1 to 27')
    return f'{SCHEME_ID}-{SCHEME_VERSION}-tier-{size}'


def profile(profile_id):
    if type(profile_id) is not str:
        raise ValueError('Unknown HQC growth tier')
    for row in PROFILES:
        if profile_for_size(row['tier']) == profile_id:
            return row
    raise ValueError('Unknown HQC growth tier')


def directory():
    return Path(os.environ.get('CRUCIBLE_HQC_GROWTH_BACKEND', str(ROOT/'.native-hqc-growth/install'))).resolve()


def artifact_manifest():
    global _artifact
    if _artifact is None:
        try:
            raw = (directory()/'build-manifest.json').read_bytes()
            if len(raw) > 262144:
                raise ValueError()
            build = json.loads(raw)
            if build['format'] != 'crucible-hqc-growth-build-v1' or build['source_commit'] != COMMIT or build['source_archive_sha256'] != ARCHIVE_SHA256:
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
            closure = {'format': 'crucible-hqc-growth-artifact-v1', 'scheme_id': SCHEME_ID,
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
            raise Unavailable('HQC growth native build missing or inconsistent') from None
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
        raise Unavailable('HQC growth native artifact changed') from None
    return artifact


def parameters(profile_id):
    row = profile(profile_id)
    return {name: row[name] for name in ('n', 'rs_length', 'rs_dimension', 'rs_delta',
        'rm_length', 'rm_repetitions', 'code_length', 'worst_case_error_weight',
        'guaranteed_decoder_error_capacity')} | {
        'secret_weight': row['weight'], 'encryption_weight': row['weight'], 'error_weight': row['weight']}


def encoding(profile_id):
    row = profile(profile_id)
    vector_bytes = (row['n']+7)//8
    return {'public_key': '32-byte seed followed by the native n-bit syndrome; lowercase hex in JSON',
        'public_key_bytes': 32+vector_bytes,
        'ciphertext': 'Native n-bit u, concatenated-code-length v, 16-byte salt; lowercase hex in JSON',
        'ciphertext_bytes': vector_bytes+row['code_length']//8+16,
        'shared_secret': '64 hexadecimal characters, the complete 32-byte KEM output', 'shared_secret_bytes': 32}


def get_manifest(profile_id):
    row = profile(profile_id); tier = row['tier']
    return {'id': profile_id, 'profile_id': profile_id, 'scheme_id': SCHEME_ID, 'version': SCHEME_VERSION,
        'name': f'HQC-derived growing-code research · Tier {tier}', 'profile_kind': 'research-tier',
        'profile_label': f'Tier {tier}', 'instance_size': tier, 'parameter_bits': row['n'],
        'parameter_label': 'ring length', 'size_parameter': dict(SIZE_PARAMETER),
        'parameters': parameters(profile_id), 'shared_secret_bytes': 32, 'encoding': encoding(profile_id),
        'record_inheritance': False, 'educational': False,
        'kdf': {'construction': 'Retained native HQC 2025-08-22 salted KEM transform'},
        'security_claim': 'Research derivative, not a named standard profile. No estimated security level, monotone attack-cost claim, IND-CCA guarantee or cross-scheme equivalence is asserted. Full shared-secret recovery tests the exact public instance.',
        'initial_security_estimate': None,
        'assumption_context': 'Hamming quasi-cyclic decoding with the exact declared low-weight distributions and primitive ring length',
        'distribution': 'Original key sampler is uniform in the XOF model using 24-bit rejection and full 256-bit seeds; its threshold and Barrett constant are recomputed for n. Encapsulation retains the published slightly biased fixed-weight sampler, full 128-bit message, 128-bit salt and 256-bit seeds. No secret-entropy truncation or agreement-conditioned resampling.',
        'algorithms': {'keygen': 'Pinned native HQC construction using the certified ring length and weight',
            'encapsulate': 'Pinned native HQC encryption with the certified RS/RM concatenated code and full message',
            'decapsulate': 'Retained RM nearest-codeword and RS decoder algorithms, re-encryption and implicit rejection'},
        'correctness': {'kind': 'perfect', 'failure_bound': '0',
            'argument': 'The honest error has weight at most E=2w²+w. Repeated RM(1,7) has distance 64r, so an erroneous outer symbol requires at least 32r flipped bits. E<32r(delta+1) permits at most delta bad symbols, corrected by RS(16+2delta,16). Truncating the ring vector cannot increase its weight. Every frozen profile satisfies this same deterministic bound.',
            'scope': 'All 27 registered profiles, conditional on successful generation and faithful implementation; availability aborts and implementation faults are separate.',
            'assumptions': 'Exact finite-field/ring arithmetic, specified nearest-codeword and RS decoding, faithful encodings and implementation',
            'evidence_status': 'Independent Sage parameter/field/codec checks, native boundary tests and honest-agreement checks; independent admission remains pending',
            'implementation_checks': 'tests/test_hqc_growth.py; one unconditioned honest decapsulation check for each issued instance'},
        'cost_tradeoff': {'growing_parameters': 'Secret/error weight, required error-correction capacity, code length and ring length',
            'cost': 'Larger public keys and ciphertexts and more ring/code arithmetic in discrete steps. Several nearby weights share the same smallest supported codec.',
            'selection_scope': 'Smallest encoding within the explicitly supported reference decoder domain, not a globally optimal HQC parameter frontier.'},
        'admission': {'status': 'local-beta research; independent admission pending', 'official_ranked': False,
            'reason': 'Versioned research parameters with fixed correctness, separate from published reference releases'},
        'implementation': {'source': 'crucible/code_hqc_v2.py', 'source_sha256': IMPLEMENTATION_SHA256,
            'artifact_sha256': artifact_manifest()['artifact_sha256'], 'version': SCHEME_VERSION,
            'runtime': 'Pinned parameter-specialized native worker with bounded process resources',
            'evidence': 'docs/hqc-growth.txt'},
        'references': [{'title': 'HQC 2025-08-22 specification', 'url': 'https://pqc-hqc.org/doc/hqc_specifications_2025_08_22.pdf'},
            {'title': 'Pinned liboqs source release', 'url': 'https://github.com/open-quantum-safe/liboqs/tree/'+COMMIT}]}


def manifests():
    return [get_manifest(profile_for_size(row['tier'])) for row in PROFILES]


def generate(profile_id):
    row = profile(profile_id); artifact = verify_artifact(profile_id); enc = encoding(profile_id)
    output = _execute(directory()/f"hqc-{row['tier']}", 'generate')
    pk, ct = enc['public_key_bytes'], enc['ciphertext_bytes']
    if len(output) != 17+pk+ct+32 or output[:4] != b'CPQ1' or struct.unpack('>III', output[4:16]) != (pk, ct, 32) or output[16] not in (0, 1):
        raise Unavailable('Invalid HQC growth worker response')
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
