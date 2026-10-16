"""Classic McEliece: published parameter sets through a pinned isolated native worker.

No smaller or reduced-entropy variants are registered. See the full artifact
manifest and docs/implementation-pq.txt for source closure and test scope.
"""
from pathlib import Path
import hashlib
from .pq_native import Adapter, GenerationLimit, Unavailable, SIZE_PARAMETER
GenerationUnavailable = Unavailable
SCHEME_ID = 'classic-mceliece'
SCHEME_VERSION = '20221023-oqs016-v1'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_DEFINITION = {'profiles': [('Classic-McEliece-348864', 261120, 96, 32, 1),
              ('Classic-McEliece-460896', 524160, 156, 32, 3),
              ('Classic-McEliece-6688128', 1044992, 208, 32, 5),
              ('Classic-McEliece-6960119', 1047319, 194, 32, 5),
              ('Classic-McEliece-8192128', 1357824, 208, 32, 5)],
 'security': 'Classic McEliece 2022-10-23 non-pc, non-f parameter sets. Kept as a '
             'structural-cryptanalysis research target, not a deployment recommendation or a claim '
             'that current structural attacks are irrelevant. The five names are discrete '
             'parameter sets, not five security levels.',
 'assumption': 'Hidden binary Goppa-code structure and fixed-weight syndrome decoding; security '
               'needs more than a generic random-code decoding claim.',
 'correctness_kind': 'perfect',
 'failure_bound': '0',
 'correctness': 'The Goppa-code decoder corrects the prescribed fixed-weight honest error vector, '
                'and both parties apply the same specified hash transform.',
 'upstream': 'liboqs 0.16.0 wrapper with vendored PQClean '
             'commit 1eacfdafc15ddc5d5759d0b85b4cef26627df181 and patches, derived from official '
             'SUPERCOP 20221025; public-domain source.',
 'limitations': 'The enclosing liboqs release is maintained, but its PQClean McEliece upstream is '
                'explicitly marked inactive. liboqs documents compiler/platform constant-time '
                'caveats for 460896/6960119. This local challenge implementation is not a '
                'side-channel or deployment endorsement. Large public keys and variable '
                'key-generation work require bounded isolated workers. Current official '
                'libmceliece is a possible separately reviewed replacement, not silently '
                'equivalent source.',
 'references': [{'title': 'Classic McEliece specification23 October 2022',
                 'url': 'https://classic.mceliece.org/mceliece-spec-20221023.pdf'},
                {'title': 'Pinned liboqs McEliece implementation and advisories',
                 'url': 'https://github.com/open-quantum-safe/liboqs/blob/5a1a854b0dc9f2141bdc771c555ee60c37950183/docs/algorithms/kem/classic_mceliece.yml'},
                {'title': 'Classic McEliece security updates',
                 'url': 'https://classic.mceliece.org/'}]}
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
