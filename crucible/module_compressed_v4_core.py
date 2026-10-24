"""Compressed module-LWE reference arithmetic, FIPS 203 at named endpoints.

Degree 256 with the published tuples implements ML-KEM-512/768. Smaller degrees
are explicitly research constructions, not FIPS-approved ML-KEM parameter sets.
The common incomplete NTT is a ring isomorphism for every admitted power-of-two
degree; at smaller degrees its quadratic factors need not be irreducible.
This variable-time Python code is for isolated research challenges and testing.
It is not a production cryptographic library or a security proof.
"""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import hmac

Q = 3329


class SamplingLimit(ValueError):
    """A bounded reference sampler did not finish; no key is returned."""


@dataclass(frozen=True)
class Parameters:
    n: int
    rank: int
    eta1: int
    eta2: int = 2
    du: int = 10
    dv: int = 4

    def __post_init__(self):
        if (type(self.n) is not int or self.n not in (8, 16, 32, 64, 128, 256)
                or type(self.rank) is not int or not 1 <= self.rank <= 3
                or type(self.eta1) is not int or self.eta1 not in (2, 3)
                or type(self.eta2) is not int or self.eta2 != 2
                or type(self.du) is not int or self.du != 10
                or type(self.dv) is not int or self.dv != 4):
            raise ValueError("Unsupported reference arithmetic parameters")

    @property
    def public_key_bytes(self):
        return self.n * self.rank * 12 // 8 + 32

    @property
    def pke_private_key_bytes(self):
        return self.n * self.rank * 12 // 8

    @property
    def private_key_bytes(self):
        return self.pke_private_key_bytes + self.public_key_bytes + 64

    @property
    def ciphertext_bytes(self):
        return self.n * (self.rank * self.du + self.dv) // 8

    @property
    def message_bytes(self):
        return self.n // 8


ML_KEM_512 = Parameters(256, 2, 3)
ML_KEM_768 = Parameters(256, 3, 2)


def domain(p, role):
    """Explicit research separation; published endpoint algorithms are unchanged."""
    if role not in ("matrix", "noise", "keygen", "public-key-hash", "encapsulation", "rejection"):
        raise ValueError("Unknown cryptographic hash purpose")
    if p in (ML_KEM_512, ML_KEM_768):
        return b""
    return (b"Crucible/module-compressed-v4/" + role.encode("ascii") + b"\0"
            + p.n.to_bytes(2,"little") + bytes([p.rank,p.eta1,p.eta2,p.du,p.dv]))


def public_key_hash(p, public_key):
    return hashlib.sha3_256(domain(p,"public-key-hash") + public_key).digest()


def _bytes(value, length, label):
    if type(value) is not bytes or len(value) != length:
        raise ValueError(f"{label} must contain exactly {length} bytes")


def _reverse(value, bits):
    result = 0
    for _ in range(bits):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


@lru_cache(maxsize=6)
def _roots(n):
    if n not in (8, 16, 32, 64, 128, 256):
        raise ValueError("Unsupported NTT degree")
    root = pow(17, 256 // n, Q)
    bits = n.bit_length() - 2
    zetas = tuple(pow(root, _reverse(i, bits), Q) for i in range(n // 2))
    gammas = tuple(pow(root, 2 * _reverse(i, bits) + 1, Q) for i in range(n // 2))
    return zetas, gammas


def ntt(poly):
    n = len(poly)
    zetas, _ = _roots(n)
    out = [value % Q for value in poly]
    index, length = 1, n // 2
    while length >= 2:
        for start in range(0, n, 2 * length):
            zeta = zetas[index]
            index += 1
            for j in range(start, start + length):
                term = zeta * out[j + length] % Q
                out[j + length] = (out[j] - term) % Q
                out[j] = (out[j] + term) % Q
        length //= 2
    return out


def inverse_ntt(poly):
    n = len(poly)
    zetas, _ = _roots(n)
    out = [value % Q for value in poly]
    index, length = n // 2 - 1, 2
    while length <= n // 2:
        for start in range(0, n, 2 * length):
            zeta = zetas[index]
            index -= 1
            for j in range(start, start + length):
                first = out[j]
                out[j] = (first + out[j + length]) % Q
                out[j + length] = zeta * (out[j + length] - first) % Q
        length *= 2
    normalizer = pow(n // 2, -1, Q)
    return [value * normalizer % Q for value in out]


def multiply_ntts(left, right):
    if len(left) != len(right):
        raise ValueError("Mismatched polynomial dimensions")
    _, gammas = _roots(len(left))
    out = []
    for i, gamma in enumerate(gammas):
        a, b = left[2*i:2*i+2]
        c, d = right[2*i:2*i+2]
        out.extend(((a*c + b*d*gamma) % Q, (a*d + b*c) % Q))
    return out


def _add(*polys):
    return [sum(values) % Q for values in zip(*polys)]


def _dot(left, right):
    return _add(*(multiply_ntts(a, b) for a, b in zip(left, right)))


def encode(values, bits):
    """FIPS ByteEncode: consecutive little-endian coefficient bits."""
    if bits not in (1, 4, 10, 12) or len(values) * bits % 8:
        raise ValueError("Unsupported encoding")
    modulus = Q if bits == 12 else 1 << bits
    accumulator = sum((value % modulus) << (bits*i) for i, value in enumerate(values))
    return accumulator.to_bytes(len(values) * bits // 8, "little")


def decode(data, bits, count, *, canonical=False):
    _bytes(data, count * bits // 8, "Encoded polynomial")
    accumulator, mask = int.from_bytes(data, "little"), (1 << bits) - 1
    values = [(accumulator >> (bits*i)) & mask for i in range(count)]
    if bits == 12:
        if canonical and any(value >= Q for value in values):
            raise ValueError("Noncanonical public-key coefficient")
        values = [value % Q for value in values]
    return values


def compress(value, bits):
    return (((value % Q) * (1 << bits) + Q // 2) // Q) % (1 << bits)


def decompress(value, bits):
    return (Q * value + (1 << (bits - 1))) >> bits


def _sample_ntt(seed, row, column, p):
    # Standard SampleNTT(rho || column || row), including 12-bit rejection.
    xof = hashlib.shake_128(domain(p,"matrix") + seed + bytes([column, row]))
    length, offset, values = 3 * p.n, 0, []
    while len(values) < p.n:
        data = xof.digest(length)
        while offset + 3 <= length and len(values) < p.n:
            a = data[offset] + 256 * (data[offset+1] & 15)
            b = (data[offset+1] >> 4) + 16 * data[offset+2]
            offset += 3
            if a < Q:
                values.append(a)
            if b < Q and len(values) < p.n:
                values.append(b)
        if len(values) < p.n:
            length *= 2
            if length > 65536:
                raise SamplingLimit("Matrix sampling resource bound exceeded")
    return values


def _noise(seed, nonce, eta, p):
    data = hashlib.shake_256(domain(p,"noise") + seed + bytes([nonce])).digest(2 * eta * p.n // 8)
    bits = [(byte >> bit) & 1 for byte in data for bit in range(8)]
    return [(sum(bits[2*eta*i:2*eta*i+eta]) - sum(bits[2*eta*i+eta:2*eta*(i+1)])) % Q
            for i in range(p.n)]


def validate_public_key(p, public_key):
    _bytes(public_key, p.public_key_bytes, "Public key")
    decode(public_key[:-32], 12, p.rank*p.n, canonical=True)


def validate_private_key(p, private_key):
    _bytes(private_key, p.private_key_bytes, "Private key")
    public_key = private_key[p.pke_private_key_bytes:-64]
    if not hmac.compare_digest(public_key_hash(p,public_key), private_key[-64:-32]):
        raise ValueError("Private-key public-key hash check failed")


def keygen_internal(p, d, z):
    _bytes(d, 32, "Key generation seed")
    _bytes(z, 32, "Rejection seed")
    expanded = hashlib.sha3_512(domain(p,"keygen") + d + bytes([p.rank])).digest()
    rho, sigma = expanded[:32], expanded[32:]
    matrix = [[_sample_ntt(rho, i, j, p) for j in range(p.rank)] for i in range(p.rank)]
    secret = [ntt(_noise(sigma, i, p.eta1, p)) for i in range(p.rank)]
    error = [ntt(_noise(sigma, p.rank+i, p.eta1, p)) for i in range(p.rank)]
    public = [_add(_dot(matrix[i], secret), error[i]) for i in range(p.rank)]
    public_key = b"".join(encode(poly, 12) for poly in public) + rho
    private_key = (b"".join(encode(poly, 12) for poly in secret) + public_key
                   + public_key_hash(p,public_key) + z)
    return public_key, private_key


def _encrypt(p, public_key, message, coins):
    _bytes(public_key, p.public_key_bytes, "Public key")
    _bytes(message, p.message_bytes, "Native message")
    _bytes(coins, 32, "Encryption coins")
    width = p.n * 12 // 8
    public = [decode(public_key[i*width:(i+1)*width], 12, p.n) for i in range(p.rank)]
    rho = public_key[-32:]
    matrix = [[_sample_ntt(rho, i, j, p) for j in range(p.rank)] for i in range(p.rank)]
    randomness = [ntt(_noise(coins, i, p.eta1, p)) for i in range(p.rank)]
    error1 = [_noise(coins, p.rank+i, p.eta2, p) for i in range(p.rank)]
    error2 = _noise(coins, 2*p.rank, p.eta2, p)
    u = [_add(inverse_ntt(_dot([matrix[j][i] for j in range(p.rank)], randomness)), error1[i])
         for i in range(p.rank)]
    mu = [decompress(bit, 1) for bit in decode(message, 1, p.n)]
    v = _add(inverse_ntt(_dot(public, randomness)), error2, mu)
    return (b"".join(encode([compress(value, p.du) for value in poly], p.du) for poly in u)
            + encode([compress(value, p.dv) for value in v], p.dv))


def _decrypt(p, pke_private_key, ciphertext):
    _bytes(pke_private_key, p.pke_private_key_bytes, "PKE private key")
    _bytes(ciphertext, p.ciphertext_bytes, "Ciphertext")
    secret_width, u_width = p.n * 12 // 8, p.n * p.du // 8
    secret = [decode(pke_private_key[i*secret_width:(i+1)*secret_width], 12, p.n) for i in range(p.rank)]
    u = [[decompress(value, p.du) for value in decode(ciphertext[i*u_width:(i+1)*u_width], p.du, p.n)]
         for i in range(p.rank)]
    v = [decompress(value, p.dv) for value in decode(ciphertext[p.rank*u_width:], p.dv, p.n)]
    product = inverse_ntt(_dot(secret, [ntt(poly) for poly in u]))
    return encode([compress(a-b, 1) for a, b in zip(v, product)], 1)


def encapsulate_internal(p, public_key, message):
    """Return (ciphertext, key); deterministic interface is for conformance tests."""
    validate_public_key(p, public_key)
    _bytes(message, p.message_bytes, "Native message")
    expanded = hashlib.sha3_512(domain(p,"encapsulation") + message + public_key_hash(p,public_key)).digest()
    return _encrypt(p, public_key, message, expanded[32:]), expanded[:32]


def decapsulate(p, private_key, ciphertext):
    validate_private_key(p, private_key)
    _bytes(ciphertext, p.ciphertext_bytes, "Ciphertext")
    split = p.pke_private_key_bytes
    public_key = private_key[split:-64]
    message = _decrypt(p, private_key[:split], ciphertext)
    expanded = hashlib.sha3_512(domain(p,"encapsulation") + message + private_key[-64:-32]).digest()
    fallback = hashlib.shake_256(domain(p,"rejection") + private_key[-32:] + ciphertext).digest(32)
    reconstructed = _encrypt(p, public_key, message, expanded[32:])
    # Python is variable-time. No externally supplied decapsulation oracle exists.
    return expanded[:32] if hmac.compare_digest(ciphertext, reconstructed) else fallback
