"""Research derivatives of real HQC and Classic McEliece native constructions.

Only published parameterized algorithms are varied, with explicit source patches.
The exact published-reference adapters remain separate and unchanged.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct

from . import code_parameters
from .pq_native import _execute,GeneratedChallenge,GenerationLimit,Unavailable
GenerationUnavailable=Unavailable
ROOT=Path(__file__).resolve().parents[1]
COMMIT='5a1a854b0dc9f2141bdc771c555ee60c37950183'
ARCHIVE_SHA256='83c6e6cff490638312e1e20ed5de593fb5d7bfab162235f6238f519d6a1feafb'
COMMON_FILES=('crucible/code_native.py','crucible/code_parameters.py','crucible/pq_native.py','scripts/code_build_backend.py','scripts/code_worker.c')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
_IMPORTED_FILES={name:sha(ROOT/name) for name in COMMON_FILES}

class Adapter:
    def __init__(self,module_path,family,scheme_id,version):
        self.path=Path(module_path);self.family=family;self.scheme_id=scheme_id;self.version=version
        self.source_sha256=sha(self.path);self._artifact=None
        values=code_parameters.HQC_WEIGHTS if family=='hqc' else code_parameters.MC_LENGTHS
        self.profiles={f'{scheme_id}-{version}-tier-{tier}':(tier,value) for tier,value in enumerate(values,1)}
    def directory(self):return Path(os.environ.get('CRUCIBLE_CODE_BACKEND',str(ROOT/'.native-code/install'))).resolve()
    def profile(self,profile_id):
        if type(profile_id) is not str or profile_id not in self.profiles:raise ValueError('Unknown research tier')
        return self.profiles[profile_id]
    def artifact_manifest(self):
        if self._artifact is None:
            try:
                raw=(self.directory()/'build-manifest.json').read_bytes()
                if len(raw)>262144:raise ValueError()
                build=json.loads(raw)
                if build['format']!='crucible-code-research-build-v1' or build['source_commit']!=COMMIT or build['source_archive_sha256']!=ARCHIVE_SHA256:raise ValueError()
                files={**_IMPORTED_FILES,str(self.path.relative_to(ROOT)):self.source_sha256}
                if any(sha(ROOT/name)!=digest for name,digest in files.items()):raise ValueError()
                if build['worker_source_sha256']!=files['scripts/code_worker.c'] or build['build_script_sha256']!=files['scripts/code_build_backend.py'] or build['parameters_sha256']!=files['crucible/code_parameters.py']:raise ValueError()
                workers={f'{self.family}-{tier}':build['workers'][f'{self.family}-{tier}'] for tier,_ in self.profiles.values()}
                for tier,value in self.profiles.values():
                    record=workers[f'{self.family}-{tier}']
                    if record['parameter']!=value or sha(self.directory()/f'{self.family}-{tier}')!=record['binary_sha256']:raise ValueError()
                closure={'format':'crucible-code-research-artifact-v1','scheme_id':self.scheme_id,'version':self.version,'files':files,'native_build_manifest_sha256':hashlib.sha256(raw).hexdigest(),'source_commit':COMMIT,'source_archive_sha256':ARCHIVE_SHA256,'source_url':build['source_url'],'workers':workers,'base_static_library_sha256':build['static_library_sha256'],'base_build_manifest_sha256':build['base_build_manifest_sha256'],'compiler':build['compiler'],'flags':build['flags']}
                closure['artifact_sha256']=hashlib.sha256(canonical(closure)).hexdigest();self._artifact=closure
            except (OSError,KeyError,ValueError,TypeError):raise Unavailable('Research native build missing or inconsistent') from None
        return deepcopy(self._artifact)
    def verify_artifact(self,profile_id=None):
        artifact=self.artifact_manifest()
        try:
            if any(sha(ROOT/path)!=digest for path,digest in artifact['files'].items()):raise ValueError()
            if sha(self.directory()/'build-manifest.json')!=artifact['native_build_manifest_sha256']:raise ValueError()
            selected=artifact['workers'] if profile_id is None else {f'{self.family}-{self.profile(profile_id)[0]}':artifact['workers'][f'{self.family}-{self.profile(profile_id)[0]}']}
            for name,record in selected.items():
                if sha(self.directory()/name)!=record['binary_sha256']:raise ValueError()
        except (OSError,ValueError):raise Unavailable('Research native artifact changed') from None
        return artifact
    def parameters(self,profile_id):
        tier,value=self.profile(profile_id)
        if self.family=='hqc':
            return {'n':17669,'rs_length':46,'rs_dimension':16,'rm_length':384,'secret_weight':value,'encryption_weight':value,'error_weight':value,'worst_case_error_weight':code_parameters.hqc_worst_case_errors(value),'guaranteed_decoder_error_capacity':1535}
        return {'m':12,'n':value,'t':64,'code_dimension':value-768,'base_field_polynomial':'x^12+x^3+1','extension_polynomial':'y^64+y^3+y+z; z is encoded as 0x002','fixed_weight_sampling_batch':code_parameters.mceliece_sampling_batch(value)}
    def encoding(self,profile_id):
        _,value=self.profile(profile_id)
        pk=2241 if self.family=='hqc' else 768*(value-768)//8
        ct=4433 if self.family=='hqc' else 96
        return {'public_key':'Exact native binary encoding, lowercase hex in JSON','public_key_bytes':pk,'ciphertext':'Exact native binary encoding, lowercase hex in JSON','ciphertext_bytes':ct,'shared_secret':'64 hexadecimal characters, the complete32-byte KEM output','shared_secret_bytes':32}
    def get_manifest(self,profile_id):
        tier,value=self.profile(profile_id);hqc=self.family=='hqc';parameters=self.parameters(profile_id)
        name='HQC-derived low-weight research' if hqc else 'Classic McEliece-derived variable-length research'
        correctness=('For w=wr=we≤27, the honest error x*r2+r1*y+e has weight at most2w²+w≤1485. Duplicated RM(1,7) has distance192, so a symbol error requires at least96 flipped bits. At most15 of46 symbols can be wrong; the unchanged RS(46,16) decoder corrects15. Truncating to17664 bits cannot increase weight. This is a deterministic bound, not an inherited standard-profile DFR estimate.' if hqc else 'The unchanged binary irreducible Goppa construction has minimum distance at least2t+1=129, so every honest weight64 error is uniquely decodable. m=12,t=64, both field polynomials and native decoder stay unchanged; only byte-aligned length768<n≤3488 varies. Successful systematic-key generation and exact native arithmetic/encoding are assumptions.')
        distribution=('Original HQC-1 key sampler targets uniform weight-w supports in the XOF model, using full 256-bit seeds. Encapsulation retains the original slightly biased fixed-weight sampler, full128-bit message, full128-bit salt and full256-bit seeds; only the mathematical weights change.' if hqc else 'Original full 256-bit seed expansion, degree-64 irreducible Goppa generation and seeded 4096-element permutation; the first n support points and exact weight-64 errors. The sampling batch follows general-parameter FixedWeight in the 2022 specification, section 5.4. No seed or secret truncation.')
        result={'id':profile_id,'profile_id':profile_id,'scheme_id':self.scheme_id,'version':self.version,'name':f'{name} · Tier {tier}','profile_kind':'research-tier','profile_label':f'Tier {tier}','record_inheritance':False,'instance_size':tier,'parameter_bits':value,'parameter_label':'Hamming weight' if hqc else 'code length','size_parameter':{'name':'Challenge tier','meaning':'Research parameter progression, not cross-scheme security bits','mapping':'w=wr=we=tier, with original HQC-1 ring and RM/RS decoder' if hqc else 'Frozen n values over50 tiers, spaced by actual log2 Prange expected trials from8 to the348864 reference; no128-bit normalization or best-known-security claim'},'parameters':parameters,'shared_secret_bytes':32,'encoding':self.encoding(profile_id),'kdf':{'construction':'Unchanged native HQC2025-08-22 KEM transform' if hqc else 'Unchanged baseline Classic McEliece SHAKE256(1 || e || C,32)'},'educational':False,'security_claim':'Research derivative, not a named published standard profile. No IND-CCA guarantee, estimated security level or cross-scheme equivalence is asserted. Full shared-secret recovery tests an individual public instance.','assumption_context':'Hamming quasi-cyclic decoding with the exact low-weight distributions' if hqc else 'Binary Goppa-code structure and fixed-weight syndrome decoding; contemporary structural attacks remain relevant','distribution':distribution,'algorithms':{'keygen':'Pinned native construction with the exact declared mathematical parameters','encapsulate':'Pinned native Encaps with OS randomness, no entropy restrictions','decapsulate':'One unconditioned honest native Decaps check; no oracle and no agreement-conditioned resampling'},'correctness':{'kind':'perfect','failure_bound':'0','argument':correctness,'scope':'All registered research parameters, conditioned on successful bounded key generation and correct implementation. This local argument is not independent formal certification.','assumptions':'Exact specified finite-field/ring arithmetic, stated code-distance/decoder properties, correct native implementation','evidence_status':'Native honest-agreement checks and analytical admissibility argument; independent review pending','implementation_checks':'tests/test_code_research.py and recorded native build checks; every runtime draw is checked once and mismatches remain visible'},'admission':{'status':'local-beta research; independent admission pending','official_ranked':False,'reason':'Actual reduced mathematical parameters, separately identified from the unchanged published reference release'},'implementation':{'source':str(self.path.relative_to(ROOT)),'source_sha256':self.source_sha256,'artifact_sha256':self.artifact_manifest()['artifact_sha256'],'version':self.version,'runtime':'Pinned parameter-specialized native code in a bounded separate process'},'references':[{'title':'Pinned liboqs source release','url':'https://github.com/open-quantum-safe/liboqs/tree/'+COMMIT},{'title':'HQC2025-08-22 specification' if hqc else 'Classic McEliece2022-10-23 specification','url':'https://pqc-hqc.org/doc/hqc_specifications_2025_08_22.pdf' if hqc else 'https://classic.mceliece.org/mceliece-spec-20221023.pdf'}]}
        if not hqc:
            result['work_reference']={'model_id':'prange-expected-trials-v1','log2_expected_trials':code_parameters.prange_log2_trials(value),'formula':'log2(binomial(n,64)/binomial(768,64))','interpretation':'Idealized inverse support-hit probability for a Prange trial; the extra factor from rejected rank-deficient submatrices is omitted','per_trial_cost':'Gaussian elimination/rank testing on a768-row binary matrix plus weight verification; excluded from the displayed trial count','exclusions':'Improved information-set decoding, low-weight techniques, structural Goppa attacks, quantum algorithms and implementation attacks. This is an elementary attack reference, not estimated security.'}
        return result
    def manifests(self):return [self.get_manifest(identifier) for identifier in self.profiles]
    def generate(self,profile_id):
        tier,_=self.profile(profile_id);artifact=self.verify_artifact(profile_id);encoding=self.encoding(profile_id)
        output=_execute(self.directory()/f'{self.family}-{tier}','generate')
        pk,ct=encoding['public_key_bytes'],encoding['ciphertext_bytes']
        if len(output)!=17+pk+ct+32 or output[:4]!=b'CPQ1' or struct.unpack('>III',output[4:16])!=(pk,ct,32) or output[16] not in (0,1):raise Unavailable('Invalid research worker response')
        self.verify_artifact(profile_id)
        public={'scheme_id':self.scheme_id,'version':self.version,'profile_id':profile_id,'profile_kind':'research-tier','profile_label':f'Tier {tier}','record_inheritance':False,'instance_size':tier,'parameter_bits':self.profile(profile_id)[1],'parameters':self.parameters(profile_id),'public_key':output[17:17+pk].hex(),'ciphertext':output[17+pk:17+pk+ct].hex(),'encoding':encoding,'implementation_sha256':self.source_sha256,'artifact_sha256':artifact['artifact_sha256']}
        return GeneratedChallenge(public,output[-32:],{'correctness_checked':True,'shared_secret_matches':bool(output[16]),'honest_trials':1,'implementation_sha256':self.source_sha256,'artifact_sha256':artifact['artifact_sha256']})
