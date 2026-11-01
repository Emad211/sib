"""ADR-027: the RFC 6238 arithmetic and the at-rest seal of the two-step secret."""

import pytest

from shift.common.seal import SealBroken, seal, unseal
from shift.domain import totp

# RFC 6238 appendix B: the ASCII secret "12345678901234567890", SHA-1, 8 digits; we keep the last six.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize(
    ("unix", "code"),
    [(59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924"), (2000000000, "279037")],
)
def test_codes_match_the_rfc_vectors(unix: int, code: str) -> None:
    assert totp.code_for(RFC_SECRET, totp.step_at(unix)) == code


def test_a_code_is_accepted_within_one_step_of_drift_and_never_twice() -> None:
    now = 1_700_000_000
    code = totp.code_for(RFC_SECRET, totp.step_at(now))
    step = totp.matching_step(RFC_SECRET, code, now)
    assert step == totp.step_at(now)
    assert totp.matching_step(RFC_SECRET, code, now + 30) == step, "one step of clock drift"
    assert totp.matching_step(RFC_SECRET, code, now + 90) is None
    assert totp.matching_step(RFC_SECRET, code, now, after_step=step) is None, "a used step is dead"
    assert totp.matching_step(RFC_SECRET, "12345", now) is None


def test_the_provisioning_uri_is_what_authenticator_apps_read() -> None:
    uri = totp.provisioning_uri("ABC", "مدیر یک")
    assert uri.startswith("otpauth://totp/Shift%3A") and "secret=ABC" in uri and "period=30" in uri


def test_a_sealed_secret_opens_only_with_its_own_key_and_untouched() -> None:
    sealed = seal("install-key", RFC_SECRET)
    assert RFC_SECRET not in sealed
    assert unseal("install-key", sealed) == RFC_SECRET
    with pytest.raises(SealBroken):
        unseal("another-key", sealed)
    tampered = sealed[:-4] + ("AAAA" if not sealed.endswith("AAAA") else "BBBB")
    with pytest.raises(SealBroken):
        unseal("install-key", tampered)
