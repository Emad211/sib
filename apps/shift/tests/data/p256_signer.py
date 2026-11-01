"""A test-only P-256 key pair and signer: what the paired phone's WebCrypto does, in plain Python."""

import base64
import hashlib
import secrets
from dataclasses import dataclass

from shift.domain import p256


@dataclass(frozen=True)
class TestKey:
    private: int
    spki_b64: str

    def sign(self, message: bytes) -> bytes:
        digest = int.from_bytes(hashlib.sha256(message).digest(), "big")
        while True:
            nonce = secrets.randbelow(p256.N - 1) + 1
            point = p256.multiply(nonce, p256.G)
            assert point is not None
            r = point[0] % p256.N
            s = pow(nonce, -1, p256.N) * (digest + r * self.private) % p256.N
            if r and s:
                return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def new_key() -> TestKey:
    private = secrets.randbelow(p256.N - 1) + 1
    public = p256.multiply(private, p256.G)
    assert public is not None
    raw = b"\x04" + public[0].to_bytes(32, "big") + public[1].to_bytes(32, "big")
    return TestKey(private, base64.b64encode(p256.SPKI_PREFIX + raw).decode("ascii"))
