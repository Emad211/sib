"""The accountant's monthly sample of the main store: which five items to count blind (owner decision 2).

The pick is a pure function of the month key and the item ids, so it is random as far as anyone can tell
yet stable all month: reloading the page, or two sessions, can never re-roll the sample until it suits
somebody. Items are ranked by a hash of `month:item`, and the first five are the sample.
"""

import hashlib
from collections.abc import Iterable

SAMPLE_SIZE = 5


def monthly_sample(month: str, item_ids: Iterable[str], *, size: int = SAMPLE_SIZE) -> tuple[str, ...]:
    """The `size` items of `month` to count, from the distinct `item_ids` given."""
    ranked = sorted(set(item_ids), key=lambda item: hashlib.sha256(f"{month}:{item}".encode()).hexdigest())
    return tuple(ranked[:size])
