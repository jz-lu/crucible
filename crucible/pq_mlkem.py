"""ML-KEM: published parameter sets through a pinned isolated native worker.

No smaller or reduced-entropy variants are registered. See the full artifact
manifest and docs/implementation-pq.txt for source closure and test scope.
"""
from pathlib import Path
import hashlib
from .pq_native import Adapter, GenerationLimit, Unavailable, SIZE_PARAMETER
GenerationUnavailable = Unavailable
SCHEME_ID = 'ml-kem'
SCHEME_VERSION = 'fips203-oqs016-v1'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_DEFINITION = {'profiles': [('ML-KEM-512', 800, 768, 32, 1),
              ('ML-KEM-768', 1184, 1088, 32, 3),
              ('ML-KEM-1024', 1568, 1568, 32, 5)],
 'security': 'FIPS 203 ML-KEM parameter sets with their stated NIST security categories. Categories '
             'are claims under the standard model, not measured bits of security. Shared-secret '
             'recovery on one hourly instance is narrower than the full IND-CCA security game.',
 'assumption': 'Module-LWE-based ML-KEM construction and its transform/hash assumptions; not a '
               'claim of equivalence between computational and decisional problems.',
 'correctness_kind': 'statistical',
 'failure_bound': 'Published estimates: ML-KEM-512 2^-138.8, ML-KEM-768 2^-164.8, ML-KEM-1024 '
                  '2^-174.8 (FIPS 203 Table 1).',
 'correctness': 'Published key-generation and noise distributions permit a very small nonzero '
                'decapsulation-failure probability; the published analytical estimates are not '
                'replaced by local sampling.',
 'upstream': 'liboqs 0.16.0, mlkem-native commit 0ba906cb14b1c241476134d7403a811b382ca498 (MIT OR '
             'Apache-2.0 OR ISC).',
 'limitations': 'Not an independent FIPS conformance certification or full security audit. Checked 180 pinned NIST ACVP keygen, encapsulation and decapsulation cases across the three sets, plus one upstream deterministic KAT per set and local honest round trips. Separate ACVP key-validation groups were not exercised. No decapsulation service is exposed.',
 'references': [{'title': 'FIPS 203, 13 August 2024',
                 'url': 'https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.203.pdf'},
                {'title': 'Pinned liboqs 0.16.0 ML-KEM source metadata',
                 'url': 'https://github.com/open-quantum-safe/liboqs/blob/5a1a854b0dc9f2141bdc771c555ee60c37950183/docs/algorithms/kem/ml_kem.yml'}]}
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
