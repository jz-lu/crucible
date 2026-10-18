"""Frozen modeled HQC research profiles; the server performs no parameter search."""
import hashlib
import json
from pathlib import Path

PARAMETERS_FILE = Path(__file__).with_name('data') / 'hqc_model_profiles.json'
PARAMETERS_SHA256 = 'f3b203d83894bbcdf8186d66119714c30caf7fd7500150560d44f2984fd36ff2'


def read_profiles():
    raw = PARAMETERS_FILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PARAMETERS_SHA256:
        raise ValueError('HQC modeled parameter certificate differs from its pin')
    return json.loads(raw)['profiles']
