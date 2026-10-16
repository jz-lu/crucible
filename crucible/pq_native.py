"""Pinned liboqs worker integration. Published parameter sets, no reduced entropy.

The short-lived worker owns secret keys and decapsulates once. Only the honest
encapsulation secret returns to the challenge service; no private-key output.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import struct
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
COMMIT='5a1a854b0dc9f2141bdc771c555ee60c37950183'
ARCHIVE_SHA256='83c6e6cff490638312e1e20ed5de593fb5d7bfab162235f6238f519d6a1feafb'
TIMEOUT_SECONDS=20
MAX_OUTPUT_BYTES=1400000+22000+64+17
SIZE_PARAMETER={'name':'Parameter set','meaning':'Named published parameter set; positions do not estimate security','unit':'parameter set','comparison_scope':'none','mapping':'Only the named published parameter sets listed in this release. Numeric positions are storage identifiers, not a security scale.'}
class GenerationLimit(RuntimeError):
    """A bounded generation attempt ended without a challenge."""
class Unavailable(RuntimeError):
    """Pinned native artifact missing, inconsistent, or unsuccessful."""
@dataclass(frozen=True,repr=False)
class GeneratedChallenge:
    public:dict
    target:bytes
    diagnostics:dict
    def __repr__(self): return 'GeneratedChallenge(public=<public inputs>, target=<redacted>, diagnostics=<private>)'
def _sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
_COMMON_SHA256=_sha(__file__)

def _execute(worker,algorithm,timeout=TIMEOUT_SECONDS):
    # The child has CPU/core/file/stack limits; the parent enforces a monotonic
    # deadline and incremental stdout bound. No stderr or inherited Python env.
    try:
        process=subprocess.Popen([str(worker),algorithm],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env={'PATH':'/usr/bin:/bin'},start_new_session=True)
    except OSError:
        raise Unavailable('Native worker could not start') from None
    output=bytearray(); deadline=time.monotonic()+timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            while selector.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0: raise GenerationLimit('Native generation reached its time limit')
                for key,_ in selector.select(min(remaining,0.25)):
                    chunk=os.read(key.fileobj.fileno(),65536)
                    if not chunk: selector.unregister(key.fileobj);continue
                    output.extend(chunk)
                    if len(output)>MAX_OUTPUT_BYTES: raise Unavailable('Native output exceeded its registered bound')
            remaining=deadline-time.monotonic()
            if remaining<=0: raise GenerationLimit('Native generation reached its time limit')
            try: status=process.wait(timeout=remaining)
            except subprocess.TimeoutExpired: raise GenerationLimit('Native generation reached its time limit') from None
        if status in {-signal.SIGXCPU,-signal.SIGALRM,-signal.SIGKILL}:
            raise GenerationLimit('Native generation reached its resource limit')
        if status: raise Unavailable('Native generation did not complete')
        return bytes(output)
    finally:
        if process.poll() is None:
            try: os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
        process.stdout.close()

class Adapter:
    def __init__(self,module_path,scheme_id,version,definition):
        self.path=Path(module_path);self.scheme_id=scheme_id;self.version=version
        self.definition=definition; self.source_sha256=_sha(self.path)
        self._artifact=None
        self.profiles={f'{scheme_id}-{version}-{row[0].lower()}':{'name':row[0],'pk':row[1],'ct':row[2],'ss':row[3],'category':row[4],'ordinal':i} for i,row in enumerate(definition['profiles'],1)}
    def _profile(self,profile_id):
        if type(profile_id) is not str or profile_id not in self.profiles: raise ValueError('Unknown published parameter set')
        return self.profiles[profile_id]
    def _directory(self): return Path(os.environ.get('CRUCIBLE_PQ_BACKEND',str(ROOT/'.native-pq/install'))).resolve()
    def artifact_manifest(self):
        if self._artifact is not None: return deepcopy(self._artifact)
        directory=self._directory(); path=directory/'build-manifest.json'
        try:
            raw=path.read_bytes()
            if len(raw)>262144: raise ValueError()
            build=json.loads(raw)
            if build.get('format')!='crucible-native-pq-build-v1' or build.get('source_commit')!=COMMIT or build.get('source_archive_sha256')!=ARCHIVE_SHA256: raise ValueError()
            files={str(self.path.relative_to(ROOT)):_sha(self.path),'crucible/pq_native.py':_sha(__file__),'scripts/build_pq_backend.c':_sha(ROOT/'scripts/build_pq_backend.c'),'scripts/build_pq_backend.py':_sha(ROOT/'scripts/build_pq_backend.py')}
            if files[str(self.path.relative_to(ROOT))]!=self.source_sha256 or files['crucible/pq_native.py']!=_COMMON_SHA256: raise ValueError()
            if build['worker_source_sha256']!=files['scripts/build_pq_backend.c'] or build['build_script_sha256']!=files['scripts/build_pq_backend.py']: raise ValueError()
            if _sha(directory/'crucible-pq-worker')!=build['binary_sha256']: raise ValueError()
            for p in self.profiles.values():
                if p['name'] not in build['algorithms'] or not re.fullmatch('[0-9a-f]{64}',build['kat_sha256'][p['name']]): raise ValueError()
            closure={'format':'crucible-pq-artifact-v1','scheme_id':self.scheme_id,'version':self.version,'files':files,'native_build_manifest_sha256':hashlib.sha256(raw).hexdigest(),'native_build':build}
            closure['artifact_sha256']=hashlib.sha256(_canonical(closure)).hexdigest()
            self._artifact=closure
        except (OSError,ValueError,KeyError,TypeError): raise Unavailable('Pinned native build is unavailable or inconsistent') from None
        return deepcopy(self._artifact)
    def _verify_artifact(self):
        closure=self.artifact_manifest();directory=self._directory()
        try:
            if any(_sha(ROOT/path)!=digest for path,digest in closure['files'].items()): raise ValueError()
            if _sha(directory/'build-manifest.json')!=closure['native_build_manifest_sha256'] or _sha(directory/'crucible-pq-worker')!=closure['native_build']['binary_sha256']: raise ValueError()
        except (OSError,ValueError): raise Unavailable('Native artifact changed since registration') from None
        return directory/'crucible-pq-worker'
    def artifact_sha256(self): return self.artifact_manifest()['artifact_sha256']
    def _encoding(self,p):
        return {'public_key':'Complete published public-key byte encoding; lowercase hexadecimal in JSON','public_key_bytes':p['pk'],'ciphertext':'Complete published ciphertext byte encoding; lowercase hexadecimal in JSON','ciphertext_bytes':p['ct'],'shared_secret':f"Exactly {2*p['ss']} hexadecimal characters ({p['ss']} complete bytes)",'shared_secret_bytes':p['ss']}
    def get_manifest(self,profile_id):
        p=self._profile(profile_id);d=self.definition
        return {'id':profile_id,'profile_id':profile_id,'scheme_id':self.scheme_id,'version':self.version,'name':p['name'],'profile_label':p['name'],'profile_kind':'standard-parameter-set','profile_order':p['ordinal'],'instance_size':p['ordinal'],'parameter_bits':0,'parameter_label':'Named published parameter set; not a bit count','size_parameter':dict(SIZE_PARAMETER),'shared_secret_bytes':p['ss'],'parameters':{'parameter_set':p['name'],'claimed_nist_category':p['category']},'encoding':self._encoding(p),'kdf':{'construction':'Exact KEM output from the pinned published construction; no additional application KDF'},'educational':False,'security_claim':d['security'],'assumption_context':d['assumption'],'distribution':'The pinned native implementation generates a fresh honest keypair and one encapsulation with operating-system randomness. No entropy restriction, parameter interpolation, or application-level correctness retries. Published key-generation rejection sampling remains part of the native construction.','algorithms':{'keygen':'Published KeyGen for '+p['name'],'encapsulate':'Published Encaps; challenge target is the entire returned shared secret','decapsulate':'One honest Decaps in the isolated worker, never exposed as an oracle'},'correctness':{'kind':d['correctness_kind'],'failure_bound':d['failure_bound'],'argument':d['correctness'],'scope':'Named published parameter set only; all implementation and machine assumptions still apply. A successful local test is not a proof of the stated analytical failure bound.','evidence_status':'Pinned upstream known-answer test and local honest round-trip checks; independent admission review pending.','implementation_checks':'Single deterministic upstream KAT per profile checked by build script; one unconditioned honest agreement check on every generated instance. Mismatch is returned to the service for quarantine, never silently retried.'},'admission':{'status':'local-beta; independent admission pending','official_ranked':False,'reason':'User-authorized local challenge; no formal admission or deployment-security endorsement.'},'implementation':{'source':str(self.path.relative_to(ROOT)),'source_sha256':self.source_sha256,'artifact_sha256':self.artifact_sha256(),'version':self.version,'runtime':'Pinned statically linked liboqs0.16.0 worker; OS randomness, bounded separate process','upstream':d['upstream'],'limitations':d['limitations']},'references':d['references']}
    def manifests(self): return [self.get_manifest(identifier) for identifier in self.profiles]
    def generate(self,profile_id):
        p=self._profile(profile_id);worker=self._verify_artifact()
        output=_execute(worker,p['name'])
        if len(output)!=17+p['pk']+p['ct']+p['ss'] or output[:4]!=b'CPQ1': raise Unavailable('Invalid native response')
        if struct.unpack('>III',output[4:16])!=(p['pk'],p['ct'],p['ss']) or output[16] not in (0,1): raise Unavailable('Invalid native response')
        pk_end=17+p['pk'];ct_end=pk_end+p['ct'];target=output[ct_end:]
        # Re-check closure after child completion, before returning an instance.
        self._verify_artifact()
        public={'scheme_id':self.scheme_id,'version':self.version,'profile_id':profile_id,'profile_label':p['name'],'profile_kind':'standard-parameter-set','instance_size':p['ordinal'],'parameter_bits':0,'parameters':{'parameter_set':p['name']},'public_key':output[17:pk_end].hex(),'ciphertext':output[pk_end:ct_end].hex(),'encoding':self._encoding(p),'kdf':{'construction':'Unmodified published KEM output'},'implementation_sha256':self.source_sha256,'artifact_sha256':self.artifact_sha256()}
        return GeneratedChallenge(public,target,{'correctness_checked':True,'shared_secret_matches':bool(output[16]),'honest_trials':1,'implementation_sha256':self.source_sha256,'artifact_sha256':self.artifact_sha256()})
    def parse_shared_secret(self,value,profile_id):
        size=self._profile(profile_id)['ss']
        if type(value) is not str or re.fullmatch('[0-9a-fA-F]{'+str(2*size)+'}',value) is None: raise ValueError('Invalid complete shared-secret encoding')
        return bytes.fromhex(value)
