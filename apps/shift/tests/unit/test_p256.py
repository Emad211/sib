"""ADR-027: the pure-Python P-256 check of the paired phone's login signature."""

import base64

from p256_signer import new_key

from shift.domain import p256


def test_a_signature_by_the_key_verifies_and_nothing_else_does() -> None:
    key, other = new_key(), new_key()
    message = b"shift-login\ndevice-1\n09120000001\n2026-11-02T08:00:00Z"
    signature = key.sign(message)

    assert p256.verify(key.spki_b64, message, signature)
    assert not p256.verify(key.spki_b64, message + b"!", signature), "another message"
    assert not p256.verify(other.spki_b64, message, signature), "another phone's key"
    assert not p256.verify(key.spki_b64, message, signature[:-1] + bytes([signature[-1] ^ 1])), "a flipped bit"
    assert not p256.verify(key.spki_b64, message, b"\x00" * 64)


def test_a_broken_public_key_is_refused() -> None:
    key = new_key()
    raw = bytearray(base64.b64decode(key.spki_b64))
    raw[-1] ^= 1  # the point leaves the curve
    assert p256.public_point(base64.b64encode(bytes(raw)).decode()) is None
    assert p256.public_point("not base64!") is None
    assert p256.public_point(key.spki_b64) is not None


def test_the_generator_is_on_the_curve_and_has_order_n() -> None:
    assert p256.on_curve(p256.G)
    assert p256.multiply(p256.N, p256.G) is None
