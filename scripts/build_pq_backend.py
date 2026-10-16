#!/usr/bin/env python3
"""Build the pinned offline runtime. Network is used only with explicit --download.
No system install; all outputs stay under --directory (default .native-pq).
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
COMMIT='5a1a854b0dc9f2141bdc771c555ee60c37950183'
ARCHIVE_SHA256='83c6e6cff490638312e1e20ed5de593fb5d7bfab162235f6238f519d6a1feafb'
URL=f'https://codeload.github.com/open-quantum-safe/liboqs/tar.gz/{COMMIT}'
ACVP={'ML-KEM-keyGen-FIPS203':'80a2f859ecdebdba349a55fd3ccb1b8b33ee9c0eab08b171f0ed1a6e3bb45fb9','ML-KEM-encapDecap-FIPS203':'e2d53b623c3ff79a52695d72ed10595ee1743ad6260ed0a3ff89d51bb5d474eb'}
ACVP_ROOT='https://raw.githubusercontent.com/usnistgov/ACVP-Server/refs/tags/v1.1.0.41/gen-val/json-files/'
ALGORITHMS=['ML-KEM-512','ML-KEM-768','ML-KEM-1024','FrodoKEM-640-SHAKE','FrodoKEM-976-SHAKE','FrodoKEM-1344-SHAKE','HQC-1','HQC-3','HQC-5','Classic-McEliece-348864','Classic-McEliece-460896','Classic-McEliece-6688128','Classic-McEliece-6960119','Classic-McEliece-8192128']
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def run(args,**kwargs): return subprocess.run([str(x) for x in args],check=True,**kwargs)
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--download',action='store_true');p.add_argument('--directory',type=Path,default=ROOT/'.native-pq');p.add_argument('--jobs',type=int,default=4);a=p.parse_args()
 if not 1<=a.jobs<=16: p.error('--jobs must be between 1 and 16')
 base=a.directory.resolve(); archive=base/'downloads/liboqs-0.16.0.tar.gz'; archive.parent.mkdir(parents=True,exist_ok=True)
 if not archive.exists():
  if not a.download: p.error('Pinned archive missing; use --download explicitly')
  request=urllib.request.Request(URL,headers={'User-Agent':'Crucible-build/1'})
  with urllib.request.urlopen(request,timeout=60) as response:
   data=response.read(12000001)
  if len(data)>12000000: raise SystemExit('Archive exceeds bound')
  archive.write_bytes(data)
 if digest(archive)!=ARCHIVE_SHA256: raise SystemExit('Source archive SHA256 mismatch')
 source=base/'source'/f'liboqs-{COMMIT}'
 if not source.exists():
  source.parent.mkdir(parents=True,exist_ok=True)
  with tarfile.open(archive) as tf:
   if sum(m.size for m in tf.getmembers())>200000000: raise SystemExit('Unpacked source exceeds bound')
   tf.extractall(source.parent,filter='data')
 # Check every extracted regular source against the immutable archive, including on rebuild.
 with tarfile.open(archive) as tf:
  expected_paths={member.name for member in tf.getmembers() if member.isfile()}
  actual_paths={str(path.relative_to(base/'source')) for path in source.rglob('*') if path.is_file()}
  if actual_paths!=expected_paths or any(path.is_symlink() for path in source.rglob('*')): raise SystemExit('Extracted source file inventory changed')
  for member in tf.getmembers():
   if member.isfile():
    target=base/'source'/member.name
    if digest(target)!=hashlib.sha256(tf.extractfile(member).read()).hexdigest(): raise SystemExit('Extracted source changed: '+member.name)
 build=base/'build'; install=base/'install';install.mkdir(parents=True,exist_ok=True)
 names=';'.join('KEM_'+n.lower().replace('-','_') for n in ALGORITHMS)
 options=['-DBUILD_SHARED_LIBS=OFF','-DOQS_USE_OPENSSL=OFF','-DOQS_BUILD_ONLY_LIB=OFF','-DOQS_DIST_BUILD=OFF','-DOQS_OPT_TARGET=generic','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_C_FLAGS_RELEASE=-O1 -DNDEBUG',f'-DOQS_MINIMAL_BUILD={names}']
 run(['cmake','-S',source,'-B',build,'-G','Ninja',*options])
 run(['cmake','--build',build,'--target','oqs','kat_kem','vectors_kem','--parallel',a.jobs])
 worker=install/'crucible-pq-worker'
 run(['cc','-std=c99','-O1','-I',build/'include',ROOT/'scripts/build_pq_backend.c',build/'lib/liboqs.a','-o',worker])
 expected=json.loads((source/'tests/KATs/kem/kats.json').read_text());kats={}
 for name in ALGORITHMS:
  result=run([build/'tests/kat_kem',name],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=150)
  actual=hashlib.sha256(result.stdout.replace(b'\r\n',b'\n')).hexdigest()
  if actual!=expected[name]['single']: raise SystemExit('KAT mismatch: '+name)
  kats[name]=actual; print('KAT passed:',name,flush=True)
 acvp_counts={}
 for stem,expected_hash in ACVP.items():
  path=base/'downloads'/(stem+'.json')
  if not path.exists():
   if not a.download: p.error('Pinned NIST ACVP vectors missing; use --download explicitly')
   with urllib.request.urlopen(urllib.request.Request(ACVP_ROOT+stem+'/internalProjection.json',headers={'User-Agent':'Crucible-build/1'}),timeout=60) as response: data=response.read(4000001)
   if len(data)>4000000: raise SystemExit('ACVP file exceeds bound')
   path.write_bytes(data)
  if digest(path)!=expected_hash: raise SystemExit('NIST vector SHA256 mismatch')
  for group in json.loads(path.read_text())['testGroups']:
   name=group['parameterSet'];function=group.get('function','keyGen')
   if function not in ('keyGen','encapsulation','decapsulation'): continue
   for case in group['tests']:
    if function=='keyGen': args=['keyGen',case['d']+case['z'],case['ek'],case['dk']]
    elif function=='encapsulation': args=['encDecAFT',case['m'],case['ek'],case['k'],case['c']]
    else: args=['encDecVAL',case['dk'],case['k'],case['c']]
    run([build/'tests/vectors_kem',name,*args],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    key=name+':'+function; acvp_counts[key]=acvp_counts.get(key,0)+1
 print('NIST ACVP vectors passed:',sum(acvp_counts.values()),flush=True)
 licenses=[]
 for path in sorted(source.rglob('*')):
  if path.is_file() and (path.name.upper().startswith('LICENSE') or path.name.upper()=='COPYING'):
   relative=path.relative_to(source);destination=install/'licenses'/relative;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
   licenses.append({'path':str(relative),'sha256':digest(path)})
 manifest={'format':'crucible-native-pq-build-v1','library':'liboqs','version':'0.16.0','source_commit':COMMIT,'source_url':URL,'source_archive_sha256':ARCHIVE_SHA256,'binary_sha256':digest(worker),'algorithms':ALGORITHMS,'cmake_options':options,'compiler':subprocess.check_output(['cc','--version'],text=True).splitlines()[0],'platform':{'system':platform.system(),'machine':platform.machine()},'kat_mode':'one deterministic upstream vector per published profile','kat_sha256':kats,'nist_acvp':{'version':'ACVP-Server v1.1.0.41','source_root':ACVP_ROOT,'sha256':ACVP,'cases_passed':acvp_counts,'excluded':'Separate encapsulationKeyCheck/decapsulationKeyCheck groups are not exercised by the upstream vectors_kem harness.'},'licenses':licenses,'build_script_sha256':digest(__file__),'worker_source_sha256':digest(ROOT/'scripts/build_pq_backend.c')}
 (install/'build-manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
 print('Pinned worker ready:',worker)
if __name__=='__main__': main()
