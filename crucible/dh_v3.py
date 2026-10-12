"""A deliberately weak, executable finite-field DH-KEM teaching adapter.

This is release 3 of an original reference specification for Crucible's educational lane,
not a standardized KEM or an implementation to protect real information.
The ladder includes deliberately weak small groups and larger research
challenges. Profile labels are subgroup-order bit lengths, never security
strengths. The special structure of the certified parameters is public.
Python integer operations are not constant-time. Correctness is algebraic;
confidentiality, chosen-ciphertext security, and implementation certification
are not claimed. No attacker-controlled source code is loaded or evaluated.

The exact distribution is x,r uniform in {1,...,q-1}, independently, using OS
randomness. Public values are g^x and g^r in the order-q subgroup of F_p*, where
p=2q+1 is prime and g=4. Identity elements are excluded. Group elements use the
modulus's fixed byte width, big endian. Malformed/non-subgroup inputs raise
ValueError; this toy adapter has no decapsulation-oracle endpoint.

The SHA-256 KDF consumes DOMAIN followed by seven length-prefixed fields:
ASCII profile ID, p, q, g, public key, ciphertext, and g^(xr). Integer fields
use the same fixed width. Each length prefix is an unsigned 32-bit big-endian
byte count. Binding the transcript and profile gives an unambiguous experiment;
this choice does not assert a formal KEM security reduction.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import hmac
from pathlib import Path
import secrets
from typing import Any


SCHEME_ID = "teaching-dh-kem"
SCHEME_VERSION = "3"
SHARED_SECRET_BYTES = 32
# A common integer input selects a registered parameter set. Its interpretation
# belongs to this scheme and release; it is independent of shared-key length.
SIZE_PARAMETER = {
    "name": "Instance size",
    "symbol": "n",
    "meaning": "Subgroup-order bit length",
    "unit": "bits",
    "mapping": "For n=20..36, choose the first prime q >= 2^(n-1)+1 for which p=2q+1 is prime, with g=4. Sizes 20..36 retain the v2 constants. For 37..128, use the registered Pocklington-certified safe primes: q=r*2^ceil(n/2)+1, for the first odd r >= 2^(n-ceil(n/2)-1)+1 satisfying the published certificates for q and p=2q+1. No prime search occurs when serving challenges.",
    "comparison_scope": "scheme-version",
}
# Release 3 extends the ladder with certified primes; existing profiles retain
# the original KDF domain and parameter bytes for reproducibility.
DOMAIN = b"crucible/teaching-dh-kem/v1/shared-secret\x00"
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@dataclass(frozen=True)
class Profile:
    id: str
    parameter_bits: int
    p: int
    q: int
    g: int = 4

    @property
    def width(self) -> int:
        return (self.p.bit_length() + 7) // 8


# The first safe-prime pair found scanning odd q upward from 2^(bits-1)+1.
# Sizes 20..36 are checked independently by trial division. The larger
# constants carry the short exact primality certificates verified below.
_PROFILES = {
    p.id: p
    for p in (
        Profile("dh-20", 20, 1048703, 524351),
        Profile("dh-21", 21, 2097779, 1048889),
        Profile("dh-22", 22, 4195259, 2097629),
        Profile("dh-23", 23, 8389163, 4194581),
        Profile("dh-24", 24, 16777907, 8388953),
        Profile("dh-25", 25, 33554519, 16777259),
        Profile("dh-26", 26, 67109543, 33554771),
        Profile("dh-27", 27, 134217827, 67108913),
        Profile("dh-28", 28, 268436867, 134218433),
        Profile("dh-29", 29, 536871263, 268435631),
        Profile("dh-30", 30, 1073742623, 536871311),
        Profile("dh-31", 31, 2147483783, 1073741891),
        Profile("dh-32", 32, 4294967387, 2147483693),
        Profile("dh-33", 33, 8589935363, 4294967681),
        Profile("dh-34", 34, 17179869263, 8589934631),
        Profile("dh-35", 35, 34359739319, 17179869659),
        Profile("dh-36", 36, 68719477403, 34359738701),
        Profile("dh-37", 37, 137605677059, 68802838529),
        Profile("dh-38", 38, 275539558403, 137769779201),
        Profile("dh-39", 39, 552782004227, 276391002113),
        Profile("dh-40", 40, 1100113510403, 550056755201),
        Profile("dh-41", 41, 2199136501763, 1099568250881),
        Profile("dh-42", 42, 4398528856067, 2199264428033),
        Profile("dh-43", 43, 8798836097027, 4399418048513),
        Profile("dh-44", 44, 17594744569859, 8797372284929),
        Profile("dh-45", 45, 35194086096899, 17597043048449),
        Profile("dh-46", 46, 70370874884099, 35185437442049),
        Profile("dh-47", 47, 140741212897283, 70370606448641),
        Profile("dh-48", 48, 281478164381699, 140739082190849),
        Profile("dh-49", 49, 562953778626563, 281476889313281),
        Profile("dh-50", 50, 1125977686016003, 562988843008001),
        Profile("dh-51", 51, 2251813101240323, 1125906550620161),
        Profile("dh-52", 52, 4503689687465987, 2251844843732993),
        Profile("dh-53", 53, 9007417492766723, 4503708746383361),
        Profile("dh-54", 54, 18014928669507587, 9007464334753793),
        Profile("dh-55", 55, 36029024115359747, 18014512057679873),
        Profile("dh-56", 56, 72059255653400579, 36029627826700289),
        Profile("dh-57", 57, 144116956528640003, 72058478264320001),
        Profile("dh-58", 58, 288230467419766787, 144115233709883393),
        Profile("dh-59", 59, 576461480300380163, 288230740150190081),
        Profile("dh-60", 60, 1152921979200733187, 576460989600366593),
        Profile("dh-61", 61, 2305861602127118339, 1152930801063559169),
        Profile("dh-62", 62, 4611705607773224963, 2305852803886612481),
        Profile("dh-63", 63, 9223380205882572803, 4611690102941286401),
        Profile("dh-64", 64, 18446751332204281859, 9223375666102140929),
        Profile("dh-65", 65, 36893506753217429507, 18446753376608714753),
        Profile("dh-66", 66, 73786983218325487619, 36893491609162743809),
        Profile("dh-67", 67, 147573958465191673859, 73786979232595836929),
        Profile("dh-68", 68, 295148009461158772739, 147574004730579386369),
        Profile("dh-69", 69, 590295944567843717123, 295147972283921858561),
        Profile("dh-70", 70, 1180591665728668565507, 590295832864334282753),
        Profile("dh-71", 71, 2361183634372790583299, 1180591817186395291649),
        Profile("dh-72", 72, 4722366971740002713603, 2361183485870001356801),
        Profile("dh-73", 73, 9444733766458633355267, 4722366883229316677633),
        Profile("dh-74", 74, 18889466400145412194307, 9444733200072706097153),
        Profile("dh-75", 75, 37778934417672428847107, 18889467208836214423553),
        Profile("dh-76", 76, 75557866977719962566659, 37778933488859981283329),
        Profile("dh-77", 77, 151115727593665646821379, 75557863796832823410689),
        Profile("dh-78", 78, 302231455307178061070339, 151115727653589030535169),
        Profile("dh-79", 79, 604462910552783470985219, 302231455276391735492609),
        Profile("dh-80", 80, 1208925820575602337382403, 604462910287801168691201),
        Profile("dh-81", 81, 2417851644493720023203843, 1208925822246860011601921),
        Profile("dh-82", 82, 4835703286616892976922627, 2417851643308446488461313),
        Profile("dh-83", 83, 9671406559001707443912707, 4835703279500853721956353),
        Profile("dh-84", 84, 19342813117044640748404739, 9671406558522320374202369),
        Profile("dh-85", 85, 38685626241020602798309379, 19342813120510301399154689),
        Profile("dh-86", 86, 77371252481742138433863683, 38685626240871069216931841),
        Profile("dh-87", 87, 154742505142994943264948227, 77371252571497471632474113),
        Profile("dh-88", 88, 309485009881475160624594947, 154742504940737580312297473),
        Profile("dh-89", 89, 618970019893273235466223619, 309485009946636617733111809),
        Profile("dh-90", 90, 1237940039371581986516762627, 618970019685790993258381313),
        Profile("dh-91", 91, 2475880078588915685796085763, 1237940039294457842898042881),
        Profile("dh-92", 92, 4951760157250311178095165443, 2475880078625155589047582721),
        Profile("dh-93", 93, 9903520314598012698132217859, 4951760157299006349066108929),
        Profile("dh-94", 94, 19807040628642364117074575363, 9903520314321182058537287681),
        Profile("dh-95", 95, 39614081258292408650773299203, 19807040629146204325386649601),
        Profile("dh-96", 96, 79228162514922426089093464067, 39614081257461213044546732033),
        Profile("dh-97", 97, 158456325028754981068363268099, 79228162514377490534181634049),
        Profile("dh-98", 98, 316912650069137130474690314243, 158456325034568565237345157121),
        Profile("dh-99", 99, 633825300119296092119641358339, 316912650059648046059820679169),
        Profile("dh-100", 100, 1267650600238049500484184571907, 633825300119024750242092285953),
        Profile("dh-101", 101, 2535301200500112194181508628483, 1267650600250056097090754314241),
        Profile("dh-102", 102, 5070602400917407694815301206019, 2535301200458703847407650603009),
        Profile("dh-103", 103, 10141204801829807386844966420483, 5070602400914903693422483210241),
        Profile("dh-104", 104, 20282409603661983667093929721859, 10141204801830991833546964860929),
        Profile("dh-105", 105, 40564819207314527789368890884099, 20282409603657263894684445442049),
        Profile("dh-106", 106, 81129638414650798957738726522883, 40564819207325399478869363261441),
        Profile("dh-107", 107, 162259276829297562690211329081347, 81129638414648781345105664540673),
        Profile("dh-108", 108, 324518553658438364084593145937923, 162259276829219182042296572968961),
        Profile("dh-109", 109, 649037107316969970695871370625027, 324518553658484985347935685312513),
        Profile("dh-110", 110, 1298074214633828035948201839165443, 649037107316914017974100919582721),
        Profile("dh-111", 111, 2596148429268797752416340608548867, 1298074214634398876208170304274433),
        Profile("dh-112", 112, 5192296858534919141674924497698819, 2596148429267459570837462248849409),
        Profile("dh-113", 113, 10384593717070200877163047848771587, 5192296858535100438581523924385793),
        Profile("dh-114", 114, 20769187434139886686643912588656643, 10384593717069943343321956294328321),
        Profile("dh-115", 115, 41538374868280763732860282458865667, 20769187434140381866430141229432833),
        Profile("dh-116", 116, 83076749736559711037890056830320643, 41538374868279855518945028415160321),
        Profile("dh-117", 117, 166153499473115801902255648161136643, 83076749736557900951127824080568321),
        Profile("dh-118", 118, 332306998946229944750466167069474819, 166153499473114972375233083534737409),
        Profile("dh-119", 119, 664613997892463283701841896696446979, 332306998946231641850920948348223489),
        Profile("dh-120", 120, 1329227995784918858970503992014012419, 664613997892459429485251996007006209),
        Profile("dh-121", 121, 2658455991569841859235052531822362627, 1329227995784920929617526265911181313),
        Profile("dh-122", 122, 5316911983139685826010615484960997379, 2658455991569842913005307742480498689),
        Profile("dh-123", 123, 10633823966279363867495231864491212803, 5316911983139681933747615932245606401),
        Profile("dh-124", 124, 21267647932558689596347091334484459523, 10633823966279344798173545667242229761),
        Profile("dh-125", 125, 42535295865117322487402900085807251459, 21267647932558661243701450042903625729),
        Profile("dh-126", 126, 85070591730235026508813476706270576643, 42535295865117513254406738353135288321),
        Profile("dh-127", 127, 170141183460469449882882719405041516547, 85070591730234724941441359702520758273),
        Profile("dh-128", 128, 340282366920938689878711368142804746243, 170141183460469344939355684071402373121),
    )
}


def _is_prime(n: int) -> bool:
    """Deterministic Miller-Rabin for unsigned integers strictly below 2^64.

    This seven-base set covers the entire stated range. Rejecting larger inputs
    is intentional: the routine must not silently become a probabilistic test.
    The adapter's largest candidate is only 37 bits.
    """
    if type(n) is not int:
        raise ValueError("Primality input must be an integer")
    if n >= 1 << 64:
        raise ValueError("Primality input exceeds this validator's range")
    if n < 2:
        return False
    for small in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % small == 0:
            return n == small
    d, powers = n - 1, 0
    while not d & 1:
        d //= 2
        powers += 1
    for base in (2, 325, 9375, 28178, 450775, 9780504, 1795265022):
        a = base % n
        if a == 0:
            continue
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(powers - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _verify_safe_prime_certificate(bits, p, q):
    """Deterministic Pocklington certificates; no probable-prime assumption.

    For q, the known factor F=2^ceil(bits/2) of q-1 is greater than
    sqrt(q). The base-3 congruence supplies both Pocklington conditions:
    3^(q-1)=1 mod q and gcd(3^((q-1)/2)-1,q)=gcd(-2,q)=1.
    Thus q is prime. For p=2q+1, the known prime factor q of p-1 is
    greater than sqrt(p). Base 2 supplies 2^(p-1)=1 mod p and
    gcd(2^((p-1)/q)-1,p)=gcd(3,p)=1. Thus p is prime.
    Reference: F. Morain, MPRI Primality Tests, Pocklington theorem:
    https://www.lix.polytechnique.fr/~morain/MPRI/2014/03x4.pdf
    """
    factor = 1 << ((bits + 1) // 2)
    if (q % 2 != 1 or (q - 1) % factor or factor * factor <= q or
            pow(3, (q - 1) // 2, q) != q - 1):
        raise ValueError("Invalid subgroup primality certificate")
    if p != 2 * q + 1 or q * q <= p or p % 3 == 0 or pow(2, p - 1, p) != 1:
        raise ValueError("Invalid safe-prime certificate")


def validate_profile(profile: Profile) -> None:
    """Reject parameters outside the exact distribution covered by the proof."""
    if not isinstance(profile, Profile):
        raise ValueError("Expected a registered parameter profile")
    if any(type(v) is not int for v in (profile.p, profile.q, profile.g, profile.parameter_bits)):
        raise ValueError("Profile parameters must be integers")
    if not 20 <= profile.parameter_bits <= 128:
        raise ValueError("Unsupported subgroup-order bit length")
    if profile.q.bit_length() != profile.parameter_bits:
        raise ValueError("Subgroup order does not match its bit-length label")
    if profile.p != 2 * profile.q + 1:
        raise ValueError("Expected a safe prime and its prime subgroup order")
    if profile.parameter_bits <= 36:
        if not _is_prime(profile.q) or not _is_prime(profile.p):
            raise ValueError("Expected prime subgroup and field orders")
    else:
        _verify_safe_prime_certificate(profile.parameter_bits, profile.p, profile.q)
    if not 1 < profile.g < profile.p - 1 or pow(profile.g, profile.q, profile.p) != 1:
        raise ValueError("Generator must have exactly the prime order q")


@lru_cache(maxsize=len(_PROFILES))
def _profile(profile_id: str) -> Profile:
    if not isinstance(profile_id, str) or profile_id not in _PROFILES:
        raise ValueError("Unknown KEM profile")
    profile = _PROFILES[profile_id]
    validate_profile(profile)
    return profile


def _encode(profile: Profile, value: int) -> bytes:
    return value.to_bytes(profile.width, "big")


def _decode_element(profile: Profile, encoded: bytes) -> int:
    if not isinstance(encoded, bytes) or len(encoded) != profile.width:
        raise ValueError("Group element must use the exact fixed-width byte encoding")
    value = int.from_bytes(encoded, "big")
    if not 1 < value < profile.p - 1 or pow(value, profile.q, profile.p) != 1:
        raise ValueError("Group element must be a nonidentity member of the order-q subgroup")
    return value


def _kdf(profile: Profile, public_key: bytes, ciphertext: bytes, shared: int) -> bytes:
    fields = (
        profile.id.encode("ascii"),
        _encode(profile, profile.p),
        _encode(profile, profile.q),
        _encode(profile, profile.g),
        public_key,
        ciphertext,
        _encode(profile, shared),
    )
    return hashlib.sha256(
        DOMAIN + b"".join(len(field).to_bytes(4, "big") + field for field in fields)
    ).digest()


def keygen(profile_id: str) -> tuple[bytes, int]:
    """Return (canonical public-key bytes, private exponent); never persist x publicly."""
    profile = _profile(profile_id)
    private_key = secrets.randbelow(profile.q - 1) + 1
    return _encode(profile, pow(profile.g, private_key, profile.p)), private_key


def encapsulate(profile_id: str, public_key: bytes) -> tuple[bytes, bytes]:
    """Return (canonical ciphertext bytes, complete 32-byte shared-secret target)."""
    profile = _profile(profile_id)
    public_value = _decode_element(profile, public_key)
    ephemeral_exponent = secrets.randbelow(profile.q - 1) + 1
    ciphertext = _encode(profile, pow(profile.g, ephemeral_exponent, profile.p))
    shared = pow(public_value, ephemeral_exponent, profile.p)
    return ciphertext, _kdf(profile, public_key, ciphertext, shared)


def decapsulate(profile_id: str, private_key: int, ciphertext: bytes) -> bytes:
    """Return K, or raise ValueError for invalid input; never expose this as an oracle."""
    profile = _profile(profile_id)
    if type(private_key) is not int or not 1 <= private_key < profile.q:
        raise ValueError("Private exponent must be an integer in [1, q-1]")
    ciphertext_value = _decode_element(profile, ciphertext)
    public_key = _encode(profile, pow(profile.g, private_key, profile.p))
    return _kdf(profile, public_key, ciphertext, pow(ciphertext_value, private_key, profile.p))


def _parameters(profile: Profile) -> dict[str, str]:
    return {name: _encode(profile, getattr(profile, name)).hex() for name in ("p", "q", "g")}


def _encoding(profile: Profile) -> dict[str, Any]:
    return {
        "group_elements": "fixed-width big-endian bytes, lowercase hexadecimal in JSON",
        "group_element_bytes": profile.width,
        "shared_secret": "exactly 64 hexadecimal characters (32 complete bytes)",
        "shared_secret_bytes": SHARED_SECRET_BYTES,
    }


def _kdf_spec() -> dict[str, Any]:
    return {
        "hash": "SHA-256",
        "domain_hex": DOMAIN.hex(),
        "framing": "domain || uint32be(len(field)) || field, repeated for each field in order",
        "fields": ["profile_id (ASCII)", "p", "q", "g", "public_key", "ciphertext", "shared_group_element"],
        "integer_encoding": "all integer fields use group_element_bytes, big endian",
    }


def serialize(profile_id: str, public_key: bytes, ciphertext: bytes) -> dict[str, Any]:
    """Build public challenge JSON from an explicit allowlist of public inputs.

    Deliberately does not accept a keypair, secret, or generated-result object.
    This avoids accidental private-key disclosure via a generic serializer.
    """
    profile = _profile(profile_id)
    _decode_element(profile, public_key)
    _decode_element(profile, ciphertext)
    return {
        "scheme_id": SCHEME_ID,
        "version": SCHEME_VERSION,
        "profile_id": profile.id,
        "instance_size": profile.parameter_bits,
        "parameter_bits": profile.parameter_bits,
        "parameters": _parameters(profile),
        "public_key": public_key.hex(),
        "ciphertext": ciphertext.hex(),
        "encoding": _encoding(profile),
        "kdf": _kdf_spec(),
        "implementation_sha256": IMPLEMENTATION_SHA256,
    }


@dataclass(frozen=True, repr=False)
class GeneratedChallenge:
    """Private generation result. Only .public is suitable for client responses."""

    public: dict[str, Any]
    target: bytes
    diagnostics: dict[str, Any]

    def __repr__(self) -> str:
        return "GeneratedChallenge(public=<public inputs>, target=<redacted>, diagnostics=<private>)"


def generate(profile_id: str) -> GeneratedChallenge:
    """Generate once, diagnose once, and preserve Encaps' K even on disagreement.

    No correctness-conditioned retries are performed. Private x and r are not
    returned. Python cannot promise erasure of their temporary in-memory values.
    The caller must store .target confidentially and never serialize this object.
    """
    public_key, private_key = keygen(profile_id)
    ciphertext, target = encapsulate(profile_id, public_key)
    recovered = decapsulate(profile_id, private_key, ciphertext)
    return GeneratedChallenge(
        public=serialize(profile_id, public_key, ciphertext),
        target=target,
        diagnostics={
            "correctness_checked": True,
            "shared_secret_matches": hmac.compare_digest(target, recovered),
            "honest_trials": 1,
            "implementation_sha256": IMPLEMENTATION_SHA256,
        },
    )


def get_manifest(profile_id: str) -> dict[str, Any]:
    """Describe one pinned teaching profile without any live challenge secrets."""
    profile = _profile(profile_id)
    return {
        "id": profile.id,
        "profile_id": profile.id,
        "scheme_id": SCHEME_ID,
        "version": SCHEME_VERSION,
        "name": f"Teaching DH-KEM · {profile.parameter_bits}-bit subgroup",
        "instance_size": profile.parameter_bits,
        "size_parameter": dict(SIZE_PARAMETER),
        "parameter_bits": profile.parameter_bits,
        "parameter_label": "subgroup-order bits",
        "modulus_bits": profile.p.bit_length(),
        "shared_secret_bytes": SHARED_SECRET_BYTES,
        "parameters": _parameters(profile),
        "encoding": _encoding(profile),
        "kdf": _kdf_spec(),
        "educational": True,
        "security_claim": "None. An uncertified research construction with a small-to-large parameter ladder; not for protecting information.",
        "assumption_context": "Finite-field discrete logarithms / computational Diffie-Hellman; no security reduction claimed for this adapter.",
        "distribution": "Independent uniform x,r in {1,...,q-1}, sampled from OS randomness. No correctness-conditioned rejection or retries.",
        "algorithms": {
            "keygen": "x <- uniform(1..q-1); pk = g^x mod p; sk = x",
            "encapsulate": "r <- uniform(1..q-1); c = g^r mod p; K = KDF(profile, pk, c, pk^r mod p)",
            "decapsulate": "validate c; pk = g^sk mod p; K = KDF(profile, pk, c, c^sk mod p)",
        },
        "correctness": {
            "kind": "perfect",
            "failure_bound": "0",
            "argument": "For every admitted p,q,g and every honest x,r, (g^x)^r = g^(xr) = (g^r)^x modulo p. Both parties hash identical profile, parameters, public key, ciphertext, and fixed-width shared element, so their 32-byte outputs agree. Since q is prime and x,r are nonzero modulo q, honest public values pass the subgroup and nonidentity checks.",
            "scope": "All registered profiles (every integer subgroup-order size from 20 through 128 bits) and all honest random choices. Claim concerns the specified arithmetic and encoding; it does not prove executable conformity or confidentiality.",
            "assumptions": "Exact modular arithmetic; stated safe-prime/group checks; identical deterministic SHA-256 computation. Collision resistance is not needed for honest agreement.",
            "evidence_status": "Local reference argument and executable tests; independent review pending.",
            "implementation_checks": "tests/test_extended.py: Pocklington certificates, boundary round trips, unchanged legacy transcripts, and malformed input. This manifest is not a test-run attestation.",
        },
        "admission": {
            "status": "teaching-only",
            "official_ranked": False,
            "reason": "Educational local lane. Independent correctness/specification review and public admission policy are pending.",
        },
        "implementation": {
            "source": "crucible/dh_v3.py",
            "source_sha256": IMPLEMENTATION_SHA256,
            "version": SCHEME_VERSION,
            "runtime": "Python standard library; variable-time integer arithmetic",
        },
        "references": [
            {"title": "Executable reference specification and its tests", "path": "crucible/dh_v3.py"},
            {"title": "NIST SP 800-227, section 2.2 (definition of KEM correctness; not approval of this adapter)", "url": "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-227.pdf"},
        ],
    }


def manifests() -> list[dict[str, Any]]:
    return [get_manifest(profile_id) for profile_id in _PROFILES]
