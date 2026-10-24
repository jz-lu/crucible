#!/usr/bin/env python3
"""Build isolated HQC modeled workers from a verified pinned liboqs source tree."""
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
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crucible.hqc_model_profiles import read_profiles
COMMIT = '5a1a854b0dc9f2141bdc771c555ee60c37950183'
ARCHIVE_SHA256 = '83c6e6cff490638312e1e20ed5de593fb5d7bfab162235f6238f519d6a1feafb'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def replace_macro(text, name, value):
    result, count = re.subn(r'(#define\s+' + re.escape(name) + r'\s+)\d+',
                            lambda match: match[1] + str(value), text)
    if count != 1:
        raise ValueError('Expected exactly one macro ' + name)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--base-native', type=Path, default=ROOT/'.native-pq')
    parser.add_argument('--output', type=Path, default=ROOT/'.local/native-hqc-model')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 8:
        parser.error('jobs must be 1..8')
    native = args.base_native.resolve()
    source = native/'source'/('liboqs-'+COMMIT)
    archive = native/'downloads/liboqs-0.16.0.tar.gz'
    if sha(archive) != ARCHIVE_SHA256:
        raise SystemExit('Original source archive mismatch')
    with tarfile.open(archive) as tf:
        expected = {m.name for m in tf.getmembers() if m.isfile()}
        actual = {str(p.relative_to(native/'source')) for p in source.rglob('*') if p.is_file()}
        if expected != actual or any(p.is_symlink() for p in source.rglob('*')):
            raise SystemExit('Original source inventory changed')
        for member in tf.getmembers():
            if member.isfile() and sha(native/'source'/member.name) != hashlib.sha256(tf.extractfile(member).read()).hexdigest():
                raise SystemExit('Original source changed')
    base = args.output.resolve()
    if not base.is_relative_to((ROOT/'.local').resolve()):
        raise SystemExit('New build output must be beneath this checkout .local directory')
    install = base/'install'; install.mkdir(parents=True, exist_ok=True)
    original = source/'src/kem/hqc/pqc-hqc_hqc-1_ref'
    library = native/'build/lib/liboqs.a'
    profiles = read_profiles()
    def build(row):
        identifier = f"hqc-{row['tier']}"
        directory = base/'source'/identifier
        if directory.exists():
            shutil.rmtree(directory)
        shutil.copytree(original, directory)
        path = directory/'parameters.h'; text = path.read_text()
        values = {'PARAM_N': row['n'], 'PARAM_N1': row['rs_length'],
            'PARAM_N2': row['rm_length'], 'PARAM_N1N2': row['code_length'],
            'PARAM_OMEGA': row['weight'], 'PARAM_OMEGA_R': row['encryption_weight'],
            'PARAM_OMEGA_E': row['error_weight'], 'PARAM_DELTA': row['rs_delta'],
            'PARAM_G': 2*row['rs_delta']+1, 'PARAM_SECURITY': 0, 'PARAM_DFR_EXP': 128,
            'PARAM_N_MU': row['barrett_mu'],
            'UTILS_REJECTION_THRESHOLD': row['rejection_threshold']}
        for name, value in values.items():
            text = replace_macro(text, name, value)
        text, count = re.subn(r'#define RS_POLY_COEFS.*?(?=\n\n#define SEED_BYTES)',
            '#define RS_POLY_COEFS ' + ', '.join(map(str, row['rs_generator'])), text, flags=re.S)
        if count != 1:
            raise ValueError('Expected exactly one RS coefficient definition')
        path.write_text(text)
        pk = 32 + (row['n']+7)//8
        ct = (row['n']+7)//8 + row['code_length']//8 + 16
        path = directory/'api.h'; text = path.read_text()
        for name, value in [('CRYPTO_PUBLICKEYBYTES', pk), ('CRYPTO_SECRETKEYBYTES', pk+80), ('CRYPTO_CIPHERTEXTBYTES', ct)]:
            text = replace_macro(text, name, value)
        path.write_text(text)
        changed = {p.name: sha(p) for p in directory.iterdir() if p.is_file() and sha(p) != sha(original/p.name)}
        worker = install/identifier
        flags = ['-DCODE_FAMILY=1', '-DHQC_ARCH_REF=1', '-DPQCHQC_NAMESPACE_PREFIX=PQCHQC_HQC1_C_', '-DUSE_OQS_RANDOMBYTES']
        command = ['cc', '-std=c99', '-O1', *flags, '-I', str(directory), '-I', str(native/'build/include'),
            '-I', str(source/'src/common/pqclean_shims'), str(ROOT/'scripts/hqc_model_worker.c'),
            *[str(p) for p in sorted(directory.glob('*.c'))], str(library), '-o', str(worker)]
        result = subprocess.run(command, capture_output=True)
        if result.returncode:
            raise RuntimeError(identifier+' build failed: '+result.stderr.decode()[:2000])
        output = subprocess.run([str(worker), 'generate'], capture_output=True, timeout=22, check=True).stdout
        if len(output) != 17+pk+ct+32 or output[:4] != b'CPQ1' or output[16] != 1:
            raise RuntimeError(identifier+' honest agreement failed')
        print('Verified', identifier, flush=True)
        return identifier, {'binary_sha256': sha(worker), 'parameters': row, 'patched_files': changed}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        records = dict(pool.map(build, profiles))
    if (install/'licenses').exists():
        shutil.rmtree(install/'licenses')
    shutil.copytree(native/'install/licenses', install/'licenses')
    runtime_files = ['crucible/hqc_model_profiles.py', 'crucible/hqc_model_correctness.py',
        'crucible/data/hqc_model_profiles.json', 'crucible/hqc_growth_parameters.py',
        'scripts/hqc_model_parameters.py', 'scripts/build_hqc_model.py', 'scripts/hqc_model_worker.c']
    manifest = {'format': 'crucible-hqc-model-build-v1', 'source_commit': COMMIT,
        'source_archive_sha256': ARCHIVE_SHA256,
        'source_url': 'https://codeload.github.com/open-quantum-safe/liboqs/tar.gz/'+COMMIT,
        'static_library_sha256': sha(library), 'base_build_manifest_sha256': sha(native/'install/build-manifest.json'),
        'files': {name: sha(ROOT/name) for name in runtime_files},
        'compiler': subprocess.check_output(['cc','--version'], text=True).splitlines()[0],
        'flags': ['-std=c99', '-O1'], 'workers': records,
        'checks': 'One unconditioned fresh honest agreement per tier at build; conditional correctness certificates and codec boundary checks are separate evidence, not inferred from these samples.'}
    (install/'build-manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2)+'\n')
    print('Isolated HQC modeled native build complete', flush=True)

if __name__ == '__main__':
    main()
