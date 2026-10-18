"""Service-side failure policy for the reviewed, immutable adapter releases.

An old exception named ``GenerationUnavailable`` may also mean changed source,
malformed output, or an inconsistent agreement flag. It is never sufficient
evidence of harmless resource exhaustion. Keep these rules outside the frozen
implementation artifacts, and review new resource exception types explicitly.
"""
from . import (
    lattice_frodo, lattice_frodo_v2, lattice_frodo_v3,
    lattice_module, lattice_module_v2, lattice_module_v3, lattice_module_v4,
    ntru_frontier, ntru_tiers, pq_native, rsa_v2, rsa_v3, rsa_v4, sntrup,
)


RESOURCE_LIMIT_TYPES = (
    rsa_v2.PrimeGenerationLimit, rsa_v3.PrimeGenerationLimit, rsa_v4.PrimeGenerationLimit,
    pq_native.GenerationLimit,
    sntrup.GenerationResourceLimit, ntru_tiers.GenerationResourceLimit,
    ntru_frontier.GenerationResourceLimit,
    lattice_frodo.GenerationLimit, lattice_frodo_v2.GenerationLimit,
    lattice_frodo_v3.GenerationLimit,
    lattice_module.GenerationLimit, lattice_module_v2.GenerationLimit,
    lattice_module_v3.GenerationLimit, lattice_module_v4.GenerationLimit,
)


class ImplementationFault(RuntimeError):
    """The registered adapter violated the service's result contract."""


def validate_result(manifest, result):
    """Validate issuance fields without logging values or interpreting evidence.

    False agreement is a valid observation, not malformed output. Exact boolean
    typing prevents strings or integers from silently authorizing issuance.
    This checks the service boundary, not a scheme's full public encoding.
    """
    if not isinstance(getattr(result, "public", None), dict):
        raise ImplementationFault("Adapter public result must be a dictionary")
    target, expected = getattr(result, "target", None), manifest.get("shared_secret_bytes")
    if type(expected) is not int or expected <= 0 or type(target) is not bytes or len(target) != expected:
        raise ImplementationFault("Adapter target violates the registered byte length")
    diagnostics = getattr(result, "diagnostics", None)
    if not isinstance(diagnostics, dict) or type(diagnostics.get("shared_secret_matches")) is not bool:
        raise ImplementationFault("Adapter agreement result must be an explicit boolean")


def failure_outcome(adapter, error):
    """Return a fixed, nonsecret ledger outcome; never inspect exception text.

    Explicit integrity declarations win even if a legacy adapter subclasses a
    resource exception for compatibility. Unknown failures require review.
    ``GenerationUnavailable`` deliberately remains a fault: its legacy contract
    cannot distinguish a launch/configuration error from a corrupted artifact.
    """
    integrity_types = tuple(kind for name in (
        "GenerationIntegrityError", "NativeImplementationError", "GenerationUnavailable"
    ) if isinstance(kind := getattr(adapter, name, None), type)
        and issubclass(kind, BaseException))
    if integrity_types and isinstance(error, integrity_types):
        return "implementation_fault"
    return "generation_abort" if isinstance(error, RESOURCE_LIMIT_TYPES) else "implementation_fault"
