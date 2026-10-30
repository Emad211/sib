"""Money is an integer Toman; floats, strings, booleans and negatives are rejected."""

import pytest
from pydantic import TypeAdapter, ValidationError

from shift.domain.money import Toman

adapter = TypeAdapter(Toman)


def test_valid_toman_values_are_plain_integers() -> None:
    assert adapter.validate_python(0) == 0
    assert adapter.validate_python(300_000) == 300_000


@pytest.mark.parametrize("value", [-1, 1.5, "1000", True, None])
def test_invalid_toman_values_are_rejected(value) -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python(value)
