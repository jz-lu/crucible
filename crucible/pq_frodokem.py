"""Salted FrodoKEM: published parameter sets through a pinned isolated native worker.

No smaller or reduced-entropy variants are registered. See the full artifact
manifest and docs/implementation-pq.txt for source closure and test scope.
"""
from pathlib import Path
import hashlib
from .pq_native import Adapter, GenerationLimit, Unavailable, SIZE_PARAMETER
GenerationUnavailable = Unavailable
SCHEME_ID = 'frodokem'
SCHEME_VERSION = 'salted-oqs016-v1'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_DEFINITION = {'profiles': [('FrodoKEM-640-SHAKE', 9616, 9752, 16, 1),
              ('FrodoKEM-976-SHAKE', 15632, 15792, 24, 3),
              ('FrodoKEM-1344-SHAKE', 21520, 21696, 32, 5)],
 'security': 'The salted FrodoKEM construction in liboqs 0.16.0 with named SHAKE parameter sets; '
             'not the earlier unsalted construction now called eFrodoKEM. No interpolation or '
             'reduced-entropy variant. A recovered shared secret is instance-specific evidence, '
             'not a measurement of the complete security game.',
 'assumption': 'Unstructured Learning With Errors with the specified noise distribution, plus the '
               'construction’s transform and hash assumptions.',
 'correctness_kind': 'statistical',
 'failure_bound': 'Published estimates for dimensions 640/976/1344: approximately 2^-138.7 / '
                  '2^-199.6 / 2^-252.5; analytical estimates, not empirically measured here.',
 'correctness': 'Honest agreement depends on the sampled error staying within the reconciliation '
                'bounds. Salt changes the multi-ciphertext construction; it does not make '
                'correctness perfect.',
 'upstream': 'liboqs 0.16.0 salted FrodoKEM, Microsoft/PQCrypto-LWEKE '
             'commit a2f9dec8917ccc3464b3378d46b140fa7353320d; MIT.',
 'limitations': 'Source identity is pinned to the released code, not a claim of complete '
                'conformance to a later Internet-Draft. Only SHAKE variants are registered; AES '
                'variants are not separate families. Shared secrets are 16, 24, 32 bytes, '
                'respectively.',
 'references': [{'title': 'FrodoKEM, IACR Communications in Cryptology 2025',
                 'url': 'https://cic.iacr.org/p/2/3/25'},
                {'title': 'liboqs 0.16.0 release: salted FrodoKEM addition',
                 'url': 'https://github.com/open-quantum-safe/liboqs/releases/tag/0.16.0'},
                {'title': 'Pinned FrodoKEM source metadata',
                 'url': 'https://github.com/open-quantum-safe/liboqs/blob/5a1a854b0dc9f2141bdc771c555ee60c37950183/docs/algorithms/kem/frodokem.yml'}]}
_adapter = Adapter(__file__, SCHEME_ID, SCHEME_VERSION, _DEFINITION)
get_manifest = _adapter.get_manifest
manifests = _adapter.manifests
generate = _adapter.generate
parse_shared_secret = _adapter.parse_shared_secret
artifact_manifest = _adapter.artifact_manifest
def verify_artifact():
    _adapter._verify_artifact()
    return artifact_manifest()
def source_bytes(): return Path(__file__).read_bytes()
