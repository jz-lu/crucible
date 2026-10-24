"""Check the architecture-dependent SQLAlchemy dependency against its metadata.

No network or native module execution: the installed distribution's METADATA
is read, and its marker is evaluated for supported development/beta targets.
"""
from importlib import metadata
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def locked_requirements():
    result = {}
    for line in (ROOT / 'requirements.lock').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            req = Requirement(line)
            name = canonicalize_name(req.name)
            assert name not in result, 'Duplicate locked package: ' + name
            result[name] = req
    return result


def pinned_version(req):
    specs = list(req.specifier)
    assert len(specs) == 1 and specs[0].operator == '==' and '*' not in specs[0].version
    return specs[0].version


@pytest.mark.parametrize('system,machine,python', [
    ('Darwin', 'arm64', '3.14'),
    ('Linux', 'x86_64', '3.13'),
    ('Darwin', 'x86_64', '3.14'),
    ('Linux', 'aarch64', '3.13'),
    ('Linux', 'ppc64le', '3.13'),
    ('Windows', 'AMD64', '3.13'),
    ('Windows', 'amd64', '3.13'),
    ('Windows', 'win32', '3.13'),
    ('Windows', 'WIN32', '3.13'),
    ('Linux', 's390x', '3.13'),
])
def test_sqlalchemy_greenlet_lock_matches_upstream_architecture_requirement(system, machine, python):
    locked = locked_requirements()
    sqlalchemy = metadata.distribution('SQLAlchemy')
    assert sqlalchemy.version == pinned_version(locked['sqlalchemy']), 'Run with the locked SQLAlchemy version'
    dependencies = [Requirement(row) for row in sqlalchemy.requires or []]
    # Extras such as asyncio are not requested by this application.
    greenlet = [row for row in dependencies if canonicalize_name(row.name) == 'greenlet'
                and row.marker is not None and 'extra' not in str(row.marker)]
    assert len(greenlet) == 1
    env = default_environment()
    env.update(platform_system=system, platform_machine=machine, python_version=python,
               python_full_version=python+'.0', extra='')
    needed = greenlet[0].marker.evaluate(env)
    pinned = locked['greenlet']
    assert pinned.marker is not None
    assert pinned.marker.evaluate(env) == needed
    assert pinned_version(pinned) in greenlet[0].specifier
    if system == 'Darwin' and machine == 'arm64':
        assert not needed, 'Keep the existing Mac development environment unchanged'
    if system == 'Linux' and machine == 'x86_64':
        assert needed, 'The beta runtime must include this native dependency'
