"""RFC 6238 time-based one-time codes for the two-step login of the head and the manager (ADR-027).

Six digits, 30-second steps, SHA-1 — what every authenticator app (Google Authenticator, Microsoft
Authenticator, the iPhone's built-in Passwords codes) reads from an `otpauth://` QR. Pure arithmetic: the
secret is generated and stored by the auth service, and the caller passes the clock.
"""

import base64
import hashlib
import hmac
import struct

STEP_SECONDS = 30
DIGITS = 6
# One step either side forgives a phone clock that is a little off, and nothing more.
DRIFT_STEPS = 1
ISSUER = "Shift"


def step_at(unix_seconds: float) -> int:
    """The 30-second time step an instant falls in."""
    return int(unix_seconds // STEP_SECONDS)


def code_for(secret_b32: str, step: int) -> str:
    """The six-digit code of one time step (RFC 4226 dynamic truncation)."""
    key = base64.b32decode(_padded(secret_b32), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def matching_step(secret_b32: str, code: str, unix_seconds: float, *, after_step: int = 0) -> int | None:
    """The step a typed code belongs to, within the drift, or None.

    `after_step` refuses a step already used, so a code seen over someone's shoulder cannot be replayed
    in the same half minute.
    """
    typed = "".join(ch for ch in code if ch.isdigit())
    if len(typed) != DIGITS:
        return None
    now = step_at(unix_seconds)
    for step in range(now - DRIFT_STEPS, now + DRIFT_STEPS + 1):
        if step > after_step and hmac.compare_digest(code_for(secret_b32, step), typed):
            return step
    return None


def base32_secret(raw: bytes) -> str:
    """A random secret as authenticator apps expect it: unpadded upper-case base32."""
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def provisioning_uri(secret_b32: str, account: str) -> str:
    """The `otpauth://` address an authenticator app reads from the QR."""
    label = _percent_encode(f"{ISSUER}:{account}")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={ISSUER}&digits={DIGITS}&period={STEP_SECONDS}"


_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _percent_encode(text: str) -> str:
    """RFC 3986 percent-encoding of a label (the domain imports no I/O or URL library)."""
    return "".join(chr(byte) if byte in _UNRESERVED else f"%{byte:02X}" for byte in text.encode("utf-8"))


def _padded(secret_b32: str) -> str:
    text = secret_b32.strip().replace(" ", "").upper()
    return text + "=" * (-len(text) % 8)
