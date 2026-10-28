"""Money in the shift domain is a non-negative integer Toman."""

from typing import Annotated

from pydantic import Field

Toman = Annotated[int, Field(strict=True, ge=0)]
