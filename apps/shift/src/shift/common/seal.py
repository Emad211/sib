"""Seal a small secret at rest with the per-install key (ADR-027): encrypt-then-MAC from the stdlib.

A stolen `shift.db` alone must not give away the two-step secrets, the same way it does not give away
the PINs (they need the per-install pepper). The key is the install's secret file; the stream is
HMAC-SHA256 in counter mode over a random nonce, and an HMAC tag over nonce and ciphertext refuses any
tampered or foreign value. Small values only (a TOTP secret is 32 characters).
"""

import base64
import hashlib
import hmac
import secrets

_NONCE = 16
_TAG = 32


class SealBroken(ValueError):
    """The sealed value was tampered with, truncated, or sealed under another install's key."""


def _keys(key: str) -> tuple[bytes, bytes]:
    root = hashlib.sha256(key.encode("utf-8")).digest()
    return (
        hmac.new(root, b"shift-seal-stream", hashlib.sha256).digest(),
        hmac.new(root, b"shift-seal-mac", hashlib.sha256).digest(),
    )


def _stream(stream_key: bytes, nonce: bytes, length: int) -> bytes:
    blocks = bytearray()
    counter = 0
    while len(blocks) < length:
        blocks += hmac.new(stream_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(blocks[:length])


def seal(key: str, plaintext: str) -> str:
    stream_key, mac_key = _keys(key)
    nonce = secrets.token_bytes(_NONCE)
    data = plaintext.encode("utf-8")
    cipher = bytes(a ^ b for a, b in zip(data, _stream(stream_key, nonce, len(data)), strict=True))
    tag = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + cipher + tag).decode("ascii")


def unseal(key: str, sealed: str) -> str:
    stream_key, mac_key = _keys(key)
    try:
        raw = base64.urlsafe_b64decode(sealed.encode("ascii"))
    except ValueError as error:
        raise SealBroken("not a sealed value") from error
    if len(raw) < _NONCE + _TAG:
        raise SealBroken("sealed value too short")
    nonce, cipher, tag = raw[:_NONCE], raw[_NONCE:-_TAG], raw[-_TAG:]
    if not hmac.compare_digest(tag, hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()):
        raise SealBroken("sealed value does not verify")
    return bytes(a ^ b for a, b in zip(cipher, _stream(stream_key, nonce, len(cipher)), strict=True)).decode("utf-8")
