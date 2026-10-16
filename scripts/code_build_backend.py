#!/usr/bin/env python3
"""Build actual parameterized derivatives of the pinned code-based KEM sources.
Requires the verified .native-pq source/static-library build; never edits it.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from crucible.code_parameters import MC_LENGTHS,HQC_WEIGHTS,mceliece_sampling_batch
COMMIT='5a1a854b0dc9f2141bdc771c555ee60c37950183'
ARCHIVE_SHA256='83c6e6cff490638312e1e20ed5de593fb5d7bfab162235f6238f519d6a1feafb'
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def replace_macro(text,name,value):
 result,count=re.subn(r'(#define\s+'+re.escape(name)+r'\s+)\d+',lambda m:m[1]+str(value),text)
 if count!=1:raise ValueError('Expected one macro '+name)
 return result

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--jobs',type=int,default=4);args=parser.parse_args()
 if not 1<=args.jobs<=8:parser.error('jobs must be1..8')
 base=ROOT/'.native-code';install=base/'install';install.mkdir(parents=True,exist_ok=True)
 native=ROOT/'.native-pq';source=native/'source'/('liboqs-'+COMMIT);archive=native/'downloads/liboqs-0.16.0.tar.gz'
 if sha(archive)!=ARCHIVE_SHA256:raise SystemExit('Original source archive mismatch')
 with tarfile.open(archive) as tf:
  expected={m.name for m in tf.getmembers() if m.isfile()}
  actual={str(p.relative_to(native/'source')) for p in source.rglob('*') if p.is_file()}
  if expected!=actual or any(p.is_symlink() for p in source.rglob('*')):raise SystemExit('Original source inventory changed')
  for member in tf.getmembers():
   if member.isfile() and sha(native/'source'/member.name)!=hashlib.sha256(tf.extractfile(member).read()).hexdigest():raise SystemExit('Original source changed')
 library=native/'build/lib/liboqs.a'
 specs=[('hqc',tier,weight) for tier,weight in enumerate(HQC_WEIGHTS,1)]+[('mceliece',tier,n) for tier,n in enumerate(MC_LENGTHS,1)]
 def build(spec):
  family,tier,value=spec;identifier=f'{family}-{tier}';directory=base/'source'/identifier
  original=source/'src/kem'/('hqc/pqc-hqc_hqc-1_ref' if family=='hqc' else 'classic_mceliece/pqclean_mceliece348864_clean')
  if directory.exists():shutil.rmtree(directory)
  shutil.copytree(original,directory)
  if family=='hqc':
   path=directory/'parameters.h';text=path.read_text()
   for macro in ('PARAM_OMEGA','PARAM_OMEGA_E','PARAM_OMEGA_R'):text=replace_macro(text,macro,value)
   # These native macros are unused by the arithmetic; do not retain false
   # standard security/DFR declarations in the derivative source.
   text=replace_macro(text,'PARAM_SECURITY',0);text=replace_macro(text,'PARAM_DFR_EXP',0);path.write_text(text)
   flags=['-DCODE_FAMILY=1','-DHQC_ARCH_REF=1','-DPQCHQC_NAMESPACE_PREFIX=PQCHQC_HQC1_C_','-DUSE_OQS_RANDOMBYTES']
  else:
   path=directory/'params.h';path.write_text(replace_macro(path.read_text(),'SYS_N',value))
   path=directory/'api.h';text=path.read_text()
   for macro,number in [('PUBLICKEYBYTES',768*(value-768)//8),('SECRETKEYBYTES',6056+value//8)]:text=replace_macro(text,'PQCLEAN_MCELIECE348864_CLEAN_CRYPTO_'+macro,number)
   path.write_text(text)
   # Follow the published general-parameter FixedWeight algorithm exactly:
   # tau=t at n=q, 2t at q/2<=n<q, 4t at q/4<=n<q/2, etc. (section 5.4).
   pool=mceliece_sampling_batch(value)
   path=directory/'encrypt.c';text=path.read_text().replace('SYS_T * 2',str(pool));path.write_text(text)
   flags=['-DCODE_FAMILY=2','-Dold_gas_syntax']
  changed={p.name:sha(p) for p in directory.iterdir() if p.is_file() and sha(p)!=sha(original/p.name)}
  worker=install/identifier
  command=['cc','-std=c99','-O1',*flags,'-I',str(directory),'-I',str(native/'build/include'),'-I',str(source/'src/common/pqclean_shims'),str(ROOT/'scripts/code_worker.c'),*[str(p) for p in sorted(directory.glob('*.c'))],str(library),'-o',str(worker)]
  result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  if result.returncode:raise RuntimeError(identifier+' build failed: '+result.stderr.decode()[:2000])
  output=subprocess.run([str(worker),'generate'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=22,check=True).stdout
  if output[:4]!=b'CPQ1' or output[16]!=1:raise RuntimeError(identifier+' honest agreement failed')
  print('Verified',identifier,flush=True)
  return identifier,{'binary_sha256':sha(worker),'parameter':value,'patched_files':changed}
 with ThreadPoolExecutor(max_workers=args.jobs) as pool: records=dict(pool.map(build,specs))
 if (install/'licenses').exists():shutil.rmtree(install/'licenses')
 shutil.copytree(native/'install/licenses',install/'licenses')
 manifest={'format':'crucible-code-research-build-v1','source_commit':COMMIT,'source_archive_sha256':ARCHIVE_SHA256,'source_url':'https://codeload.github.com/open-quantum-safe/liboqs/tar.gz/'+COMMIT,'static_library_sha256':sha(library),'base_build_manifest_sha256':sha(native/'install/build-manifest.json'),'worker_source_sha256':sha(ROOT/'scripts/code_worker.c'),'build_script_sha256':sha(__file__),'parameters_sha256':sha(ROOT/'crucible/code_parameters.py'),'compiler':subprocess.check_output(['cc','--version'],text=True).splitlines()[0],'flags':['-std=c99','-O1'],'workers':records,'checks':'One fresh unconditioned honest agreement per profile at build; additional tests are recorded separately.'}
 (install/'build-manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
 print('Research native build complete',flush=True)
if __name__=='__main__':main()
