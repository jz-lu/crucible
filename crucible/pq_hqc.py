"""HQC dated 2025-08-22: published parameter sets through a pinned isolated native worker.

No smaller or reduced-entropy variants are registered. See the full artifact
manifest and docs/implementation-pq.txt for source closure and test scope.
"""
from pathlib import Path
import hashlib
from .pq_native import Adapter, GenerationLimit, Unavailable, SIZE_PARAMETER
GenerationUnavailable = Unavailable
SCHEME_ID = 'hqc'
SCHEME_VERSION = '20250822-oqs016-v1'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_DEFINITION = {'profiles': [('HQC-1', 2241, 4433, 32, 1),
              ('HQC-3', 4514, 8978, 32, 3),
              ('HQC-5', 7237, 14421, 32, 5)],
 'security': 'HQC team specification 2025-08-22 as implemented in liboqs 0.16.0. This is explicitly '
             'not the newer 2026-10-12 specification and not a final NIST FIPS. Published '
             'categories do not mean that an hourly recovery tests every security property.',
 'assumption': 'Quasi-cyclic syndrome-decoding and the exact HQC transform/hash assumptions; '
               'distinct structured-code construction from Classic McEliece.',
 'correctness_kind': 'statistical',
 'failure_bound': 'Dated 2025-08-22 Table 5 claims <2^-128 / <2^-192 / <2^-256 for HQC-1/-3/-5. These are historical analytical claims, not revalidated guarantees; later specification revisions change the parameters and failure analysis.',
 'correctness': 'The published concatenated-code decoder can fail on rare honest error patterns. '
                'The service observes one honest agreement check without conditioning the '
                'challenge distribution on success.',
 'upstream': 'liboqs 0.16.0, official HQC commit 161cd4fdf6b4a5198cf40b3a1243f9f27f13e03d with '
             'liboqs copy-from-upstream patches; public-domain source.',
 'limitations': 'Dated construction is intentional because matching released code for the '
                'October 2026 specification was not established. No automatic upgrade or '
                'cross-release credit. Full independent implementation/constant-time review '
                'remains pending.',
 'references': [{'title': 'HQC specification 2025-08-22',
                 'url': 'https://pqc-hqc.org/doc/hqc_specifications_2025_08_22.pdf'},
                {'title': 'Pinned HQC source and specification metadata',
                 'url': 'https://github.com/open-quantum-safe/liboqs/blob/5a1a854b0dc9f2141bdc771c555ee60c37950183/docs/algorithms/kem/hqc.yml'}]}
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
