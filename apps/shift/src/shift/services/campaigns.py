"""The campaign calendar behind the shift event «ت» (S01 §3, S03 §1).

The clinic's discount comes from this list and nowhere else: the manager defines a campaign ahead of
time with a name, the service it covers, the amount and the days it runs, and the phone may then name
it in a `campaign.discount_applied` event. A row is configuration — not a record of the clinic — so
saving the same id again fixes a typo instead of stacking a second version, and the event log is left
append-only (R-03).

The bounds below are the manager's own guard rails, refused before anything is written: a blank name
or service, a negative or absurdly large amount, and a date range that is malformed or runs backwards.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from shift.adapters.db import campaign_repo
from shift.adapters.db.campaign_repo import CampaignRow
from shift.adapters.db.core import Database
from shift.common.clock import Clock, SystemClock, utc_iso
from shift.domain.policy import PolicyError

# One campaign may not discount more than this; a larger figure is a typo, not a decision.
MAX_AMOUNT_TOMAN = 100_000_000
_LABEL_MAX = 80
# The id travels in a URL path and in an event payload, so it stays one readable Latin token.
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class CampaignError(ValueError):
    """The campaign this caller described is not one the clinic could apply."""


@dataclass(frozen=True, slots=True)
class Campaign:
    """One campaign: the amount, the service it covers and the days it runs."""

    campaign_id: str
    name: str
    service: str
    amount_toman: int
    starts_on: date
    ends_on: date
    created_by: str
    created_at: str


def _identifier(value: object) -> str:
    text = str(value).strip()
    if _IDENTIFIER.fullmatch(text) is None:
        raise CampaignError(f"campaign_id must be 1..64 letters, digits, '_' or '-': {value!r}")
    return text


def _label(value: object, field: str) -> str:
    text = str(value).strip()
    if not 1 <= len(text) <= _LABEL_MAX:
        raise CampaignError(f"{field} must be 1..{_LABEL_MAX} characters")
    return text


def _amount(value: object) -> int:
    """A whole, non-negative Toman amount no larger than one campaign may discount."""
    if isinstance(value, bool) or isinstance(value, float):
        raise CampaignError(f"amount_toman must be a whole number, got {value!r}")
    try:
        number = int(str(value).strip())
    except ValueError as exc:
        raise CampaignError(f"amount_toman must be a whole number, got {value!r}") from exc
    if number < 0:
        raise CampaignError(f"amount_toman must not be negative, got {number}")
    if number > MAX_AMOUNT_TOMAN:
        raise CampaignError(f"amount_toman must be at most {MAX_AMOUNT_TOMAN:,}, got {number:,}")
    return number


def _day(value: object, field: str) -> date:
    """A stored day, ISO `YYYY-MM-DD`; anything else would compare wrong against the shift date."""
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise CampaignError(f"{field} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


def _actor(actor_id: str) -> str:
    """Every row names its author; an empty actor means a broken caller (R-04)."""
    actor = actor_id.strip()
    if not actor:
        raise PolicyError("actor_id is required")
    return actor


def _campaign(row: CampaignRow) -> Campaign:
    return Campaign(
        campaign_id=row.campaign_id,
        name=row.name,
        service=row.service,
        amount_toman=row.amount_toman,
        starts_on=date.fromisoformat(row.starts_on),
        ends_on=date.fromisoformat(row.ends_on),
        created_by=row.created_by,
        created_at=row.created_at,
    )


class CampaignService:
    """Reads the campaign calendar and lets a manager define or fix one campaign."""

    def __init__(self, database: Database, *, clock: Clock | None = None) -> None:
        self._database = database
        self._clock = clock or SystemClock()

    def save(
        self,
        campaign_id: object,
        *,
        name: object,
        service: object,
        amount_toman: object,
        starts_on: object,
        ends_on: object,
        actor_id: str,
    ) -> Campaign:
        """Define or fix one campaign; the same id replaces the row instead of adding a second one."""
        resolved_id = _identifier(campaign_id)
        label = _label(name, "name")
        covered = _label(service, "service")
        amount = _amount(amount_toman)
        first = _day(starts_on, "starts_on")
        last = _day(ends_on, "ends_on")
        if first > last:
            raise CampaignError(f"starts_on must not be after ends_on ({first.isoformat()} > {last.isoformat()})")
        actor = _actor(actor_id)
        values: dict[str, Any] = {
            "campaign_id": resolved_id,
            "name": label,
            "service": covered,
            "amount_toman": amount,
            "starts_on": first.isoformat(),
            "ends_on": last.isoformat(),
            "created_by": actor,
            "created_at": utc_iso(self._clock.now_utc()),
        }
        with self._database.write() as connection:
            return _campaign(campaign_repo.upsert(connection, values))

    def all(self) -> list[Campaign]:
        """Every campaign, newest first: the list the manager edits and the phone reads."""
        with self._database.read() as connection:
            return [_campaign(row) for row in campaign_repo.newest_first(connection)]

    def active_on(self, day: date) -> list[Campaign]:
        """The campaigns that include `day`, by name: which discount that night may apply."""
        with self._database.read() as connection:
            return [_campaign(row) for row in campaign_repo.active_on(connection, day.isoformat())]

    def get(self, campaign_id: object) -> Campaign | None:
        """One campaign by id, or None: an id that cannot name a stored row is simply not found."""
        text = str(campaign_id).strip()
        if _IDENTIFIER.fullmatch(text) is None:
            return None
        with self._database.read() as connection:
            row = campaign_repo.get(connection, text)
        return None if row is None else _campaign(row)
