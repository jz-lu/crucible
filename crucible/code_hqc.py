"""Explicit research derivative; published reference release remains unchanged."""
from pathlib import Path
import hashlib
from .code_native import Adapter,GenerationLimit,GenerationUnavailable
SCHEME_ID='hqc'
SCHEME_VERSION='weight-research-v1'
IMPLEMENTATION_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SIZE_PARAMETER={'name':'Challenge tier','meaning':'Research parameters, not comparable security bits','mapping':'w=wr=we=tier for tiers1..27, n=17669, unchanged RM/RS decoder'}
_adapter=Adapter(__file__,'hqc',SCHEME_ID,SCHEME_VERSION)
get_manifest=_adapter.get_manifest
manifests=_adapter.manifests
generate=_adapter.generate
artifact_manifest=_adapter.artifact_manifest
verify_artifact=_adapter.verify_artifact
def source_bytes():return Path(__file__).read_bytes()
