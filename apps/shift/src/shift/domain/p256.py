"""ECDSA P-256 signature check for the paired phone's login proof (ADR-027), pure arithmetic.

The phone made an ECDSA P-256 key pair when it was paired (`enroll.html`) and the server kept the public
half (SPKI). A login from outside the clinic signs a short message with the private half, so a PIN and a
code typed on some other device are not enough. The standard library has no ECDSA, and the check is a
few point multiplications, so it lives here: SHA-256, IEEE P1363 signatures (`r || s`, what WebCrypto
returns), SPKI public keys.
"""

import base64
import binascii
import hashlib

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
G = (
    0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
)
# DER header of a P-256 SubjectPublicKeyInfo with an uncompressed point (id-ecPublicKey, prime256v1).
SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")

Point = tuple[int, int] | None


def on_curve(point: tuple[int, int]) -> bool:
    x, y = point
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x + A * x + B)) % P == 0


def add(first: Point, second: Point) -> Point:
    if first is None:
        return second
    if second is None:
        return first
    (x1, y1), (x2, y2) = first, second
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if first == second:
        slope = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    return x3, (slope * (x1 - x3) - y1) % P


def multiply(scalar: int, point: Point) -> Point:
    result: Point = None
    addend = point
    while scalar:
        if scalar & 1:
            result = add(result, addend)
        addend = add(addend, addend)
        scalar >>= 1
    return result


def public_point(spki_b64: str) -> tuple[int, int] | None:
    """The point inside a base64 SPKI public key, or None when it is not a valid P-256 key."""
    try:
        der = base64.b64decode(spki_b64, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(der) != len(SPKI_PREFIX) + 65 or not der.startswith(SPKI_PREFIX) or der[len(SPKI_PREFIX)] != 0x04:
        return None
    raw = der[len(SPKI_PREFIX) + 1 :]
    point = (int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    return point if on_curve(point) else None


def verify(spki_b64: str, message: bytes, signature: bytes) -> bool:
    """Whether `signature` (64 bytes, r || s) is this key's ECDSA-SHA256 signature of `message`."""
    public = public_point(spki_b64)
    if public is None or len(signature) != 64:
        return False
    r, s = int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    if not (1 <= r < N and 1 <= s < N):
        return False
    digest = int.from_bytes(hashlib.sha256(message).digest(), "big")
    w = pow(s, -1, N)
    combined = add(multiply(digest * w % N, G), multiply(r * w % N, public))
    return combined is not None and combined[0] % N == r
