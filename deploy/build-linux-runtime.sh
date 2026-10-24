#!/usr/bin/env bash
# Explicit build steps in an isolated Linux checkout. This never admits artifacts.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash deploy/build-linux-runtime.sh --worktree /absolute/build/checkout \
         --step prerequisites|pq|code|hqc-growth|hqc-model|sntrup|inventory \
         [--jobs 2] [--download]

Read docs/linux-runtime-build.txt first. The dedicated checkout must contain
.crucible-linux-build-worktree with the line crucible-isolated-linux-build-v1.
--download permits only the builders' digest-checked source/vector downloads;
without it, populate their exact source caches in advance. No package install,
database access, admission, pin replacement, service startup or deployment.
EOF
}
die() { printf '%s\n' "$*" >&2; exit 1; }
worktree='' step='' jobs=2 download=()
while (($#)); do
  case "$1" in
    --worktree|--step|--jobs)
      (($# >= 2)) || die "Missing argument for $1"
      case "$1" in
        --worktree) worktree=$2;; --step) step=$2;; --jobs) jobs=$2;;
      esac
      shift 2;;
    --download) download=(--download); shift;;
    --help|-h) usage; exit 0;;
    *) die "Unknown option; use --help.";;
  esac
done
[[ $(uname -s) == Linux ]] || die 'Run this builder inside the dedicated Linux VM.'
[[ $worktree == /* && -d $worktree ]] || die 'Supply an existing absolute --worktree.'
[[ $jobs =~ ^[1-8]$ ]] || die '--jobs must be between 1 and 8.'
case "$step" in prerequisites|pq|code|hqc-growth|hqc-model|sntrup|inventory) ;;
  *) die 'Select one supported --step; use --help.';; esac
if ((${#download[@]})) && [[ $step != pq && $step != sntrup ]]; then
  die '--download applies only to pq or sntrup.'
fi
worktree=$(cd "$worktree" && pwd -P)
[[ -f $worktree/.crucible-linux-build-worktree &&
   $(cat "$worktree/.crucible-linux-build-worktree") == crucible-isolated-linux-build-v1 ]] ||
  die 'Missing isolated-worktree marker; do not run in a live service checkout.'
python=$worktree/.venv/bin/python
[[ -x $python && -f $worktree/crucible/registry.py ]] || die 'Prepare the checkout and its .venv first.'

# Inspect names only: never echo potentially secret values. A build shell must
# not inherit the live service's configuration or database credentials.
while IFS= read -r name; do
  case "$name" in CRUCIBLE_*|DATABASE_URL|PGPASSWORD|PGPASSFILE|PGSERVICE|PGSERVICEFILE)
    die 'Use a clean build shell without service/database environment variables.';; esac
done < <(compgen -e)

# Builders have fixed output roots. Refuse symlinks and any existing .local
# contents except this isolated HQC build, rather than touching user state.
"$python" -I -B - "$worktree" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
for name in ('.native-pq', '.native-code', '.native-hqc-growth', '.native',
             '.local', '.local/native-hqc-model', '.linux-build-home'):
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise SystemExit('Build roots must remain inside the isolated checkout.')
    if path.is_dir() and any(child.is_symlink() and
            not child.resolve().is_relative_to(root) for child in path.rglob('*')):
        raise SystemExit('Build storage contains a symlink outside the isolated checkout.')
local = root / '.local'
if local.exists() and (not local.is_dir() or
        any(path.name != 'native-hqc-model' for path in local.iterdir())):
    raise SystemExit('This checkout contains local state; use a fresh build-only copy.')
PY
cd "$worktree"

if [[ $step == prerequisites ]]; then
  for command in cc make cmake ninja pkg-config; do
    command -v "$command" >/dev/null || die "Missing build prerequisite: $command"
  done
  "$python" -I -B -c 'import sys, importlib.metadata as m; assert sys.version_info >= (3,11); print("Python", sys.version.split()[0]); print("cryptography", m.version("cryptography"))'
  cc --version | sed -n '1p'
  cmake --version | sed -n '1p'
  pkg-config --modversion openssl
  printf '%s\n' 'Prerequisite commands found; exact build provenance and native compatibility are not approved.'
  exit 0
fi

if [[ $step == inventory ]]; then
  "$python" -I -B - "$worktree" <<'PY'
import hashlib, importlib.metadata, json, platform, sys
from pathlib import Path
root = Path(sys.argv[1])
files, missing = {}, []
directories = ('.native-pq/install', '.native-code/install', '.native-hqc-growth/install',
               '.local/native-hqc-model/install')
for name in directories:
    directory = root / name
    if not directory.is_dir():
        missing.append(name)
        continue
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise SystemExit('Refusing symlinked runtime inventory.')
        if path.is_file():
            files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
for name in ('sntrup', 'ntru-tiers', 'ntru-frontier'):
    worker = {'sntrup': 'crucible-sntrup-worker', 'ntru-tiers': 'crucible-ntru-tier-worker',
              'ntru-frontier': 'crucible-ntru-frontier-worker'}[name]
    for leaf in (worker, 'build-manifest.json'):
        relative = f'.native/{name}/{leaf}'
        path = root / relative
        if path.is_symlink():
            raise SystemExit('Refusing symlinked runtime inventory.')
        if path.is_file():
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            missing.append(relative)
distribution = importlib.metadata.distribution('cryptography')
wheel_files = {}
for name in ('cryptography/hazmat/bindings/_rust.abi3.so',
             'cryptography/hazmat/primitives/asymmetric/x25519.py'):
    path = Path(distribution.locate_file(name))
    if path.is_file():
        wheel_files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        missing.append(name)
print(json.dumps({'format': 'crucible-linux-build-observation-v1',
    'platform': {'system': platform.system(), 'machine': platform.machine(),
                 'python': platform.python_version()},
    'files_sha256': files, 'missing_paths': missing,
    'cryptography': {'version': distribution.version, 'files_sha256': wheel_files},
    'approved': False, 'registry_validated': False,
    'scope': 'Observed file hashes only. No source-to-binary equivalence, dependency closure, conformance or admission is inferred.'}, indent=2))
PY
  exit 0
fi

# Isolate upstream build subprocesses from personal environment and home files.
# Native builder code/parameters and their digest checks remain unchanged.
mkdir -p "$worktree/.linux-build-home"
clean=(env -i "HOME=$worktree/.linux-build-home"
  'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
  'LC_ALL=C.UTF-8' 'PYTHONDONTWRITEBYTECODE=1' 'PYTHONNOUSERSITE=1')
case "$step" in
  pq) "${clean[@]}" "$python" -B scripts/build_pq_backend.py \
        --directory "$worktree/.native-pq" --jobs "$jobs" "${download[@]}";;
  code) "${clean[@]}" "$python" -B scripts/code_build_backend.py --jobs "$jobs";;
  hqc-growth) "${clean[@]}" "$python" -B scripts/build_hqc_growth.py \
        --base-native "$worktree/.native-pq" --jobs "$jobs";;
  hqc-model) "${clean[@]}" "$python" -B scripts/build_hqc_model.py \
        --base-native "$worktree/.native-pq" --output "$worktree/.local/native-hqc-model" --jobs "$jobs";;
  sntrup) "${clean[@]}" "$python" -B scripts/build_sntrup.py \
        --source-cache "$worktree/.native/source-cache" --openssl-prefix /usr \
        --reference --jobs "$jobs" "${download[@]}";;
esac
printf '%s\n' 'Build step finished. Outputs remain unapproved; no runtime pins, admissions or user data were changed.'
