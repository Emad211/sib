"""The card's C1 sections that the owner's document names and the first seven did not carry (S01 §6).

Three small, read-only sections live here so `daily_card.py` does not grow past what one file can hold:

- `event_counts` — «رویدادهای ۲۴ ساعت»: how many records each event code had in the window. The details
  behind a count are the `shift_events` rows of the same code, so the page opens them without a second
  copy of the data.
- `overdue_faults` — «ارجاع‌های مهلت‌گذشته»: an assigned equipment fault nobody has fixed whose deadline
  is before the day the card is built.
- `call_sample` — «نمونهٔ تماس» (owner decision 5): a few patient registrations of the covered days,
  drawn at random, for the manager's control call. The accounting mirror keeps no phone number and no
  name (S04), so the card and its stored document carry only the patient id; the name and mobile are
  read from accounting when the page is opened, and never stored. The draw is seeded by the card's
  own date, so rebuilding a card never changes who is on it.

Nothing here reads a reference figure: a count, a deadline and a registration are not the blind-count
target (ADR-011).
"""

import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection

from shift.adapters.db import mirror_repo
from shift.common.clock import TEHRAN, parse_iso, to_tehran
from shift.domain.daily_card import CardWindow
from shift.domain.events import EventType

CALL_SAMPLE_SIZE = 3
UNKNOWN_REGISTRAR = "نامشخص"
_ASSIGNED = EventType.EQUIPMENT_FAULT_ASSIGNED.value
_REPORTED = EventType.EQUIPMENT_FAULT_REPORTED.value
_FIXED = EventType.EQUIPMENT_FAULT_FIXED.value


@dataclass(frozen=True, slots=True)
class EventCountLine:
    """One event code and how many records it had in the window."""

    code: str
    label_fa: str
    count: int

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "label_fa": self.label_fa, "count": self.count}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "EventCountLine":
        return cls(str(payload["code"]), str(payload["label_fa"]), int(payload["count"]))


@dataclass(frozen=True, slots=True)
class OverdueFaultLine:
    """An assigned fault past its deadline: the device, who owes the repair and how late it is."""

    device: str
    assignee: str
    due_on: str
    days_late: int

    def to_payload(self) -> dict[str, Any]:
        return {"device": self.device, "assignee": self.assignee, "due_on": self.due_on, "days_late": self.days_late}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "OverdueFaultLine":
        return cls(str(payload["device"]), str(payload["assignee"]), str(payload["due_on"]), int(payload["days_late"]))


@dataclass(frozen=True, slots=True)
class CallSampleLine:
    """One patient registration picked for a control call — the patient's id and nothing else.

    A name or a mobile on the card would be patient data stored in the shift database and copied into
    every card document, so the card keeps the id and the page reads the rest from accounting when it
    is opened (owner decision 5, S04).
    """

    patient_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"patient_id": self.patient_id}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CallSampleLine":
        # Cards written before v2 kept `patient_number`, which is the same accounting patient id.
        if "patient_id" in payload:
            return cls(str(payload["patient_id"]))
        return cls(str(payload["patient_number"]))


def event_counts(rows: Sequence[Any]) -> tuple[EventCountLine, ...]:
    """Count the window's shift-event rows per code, the busiest code first and ties by code."""
    counts: Counter[tuple[str, str]] = Counter((row.code, row.label_fa) for row in rows)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0][0]))
    return tuple(EventCountLine(code, label, count) for (code, label), count in ordered)


def overdue_faults(events: Sequence[Any], moment: datetime, person_name: Any) -> tuple[OverdueFaultLine, ...]:
    """Every assigned fault not yet fixed whose deadline day is before today (Tehran), most late first."""
    today = to_tehran(moment).date()
    fixed = {event.ref_event_id for event in events if event.type == _FIXED and event.ref_event_id}
    reported = {event.event_id: event for event in events if event.type == _REPORTED}
    latest: dict[str, Any] = {}
    for event in events:
        if event.type == _ASSIGNED and event.ref_event_id in reported and event.ref_event_id not in fixed:
            latest[event.ref_event_id] = event
    lines: list[OverdueFaultLine] = []
    for fault_id, assignment in latest.items():
        due = date.fromisoformat(str(assignment.payload["due_on"]))
        if due >= today:
            continue
        device = str(reported[fault_id].payload.get("device", ""))
        lines.append(
            OverdueFaultLine(
                device, person_name(str(assignment.payload["assignee_id"])), due.isoformat(), (today - due).days
            )
        )
    return tuple(sorted(lines, key=lambda line: (-line.days_late, line.device)))


def _registered_at(text: str) -> datetime:
    """A mirror stamp as a Tehran instant; accounting writes clinic wall time, so a bare stamp is Tehran's."""
    try:
        return to_tehran(parse_iso(text))
    except ValueError:
        return datetime.fromisoformat(text).replace(tzinfo=TEHRAN)


def call_sample(
    connection: Connection, window: CardWindow, *, size: int = CALL_SAMPLE_SIZE
) -> tuple[CallSampleLine, ...]:
    """A seeded random draw of the window's valid registrations, oldest first once drawn.

    Only a registration with both checks passed is a candidate: it is the one the clinic rewards, so
    it is the one a control call is for (owner decision 5). The row keeps the id alone; the name and
    mobile are accounting's answer at the moment the page is opened.
    """
    rows = connection.exec_driver_sql(
        "SELECT patient_id, created_at "
        f"FROM {mirror_repo.REGISTRATION} "
        "WHERE has_valid_nid = 1 AND has_valid_phone = 1 ORDER BY created_at, patient_id"
    ).mappings()
    inside = []
    for row in rows:
        stamp = _registered_at(str(row["created_at"]))
        if window.covered_from <= stamp.date() <= window.covered_to:
            inside.append(str(row["patient_id"]))
    picked = random.Random(window.card_date.toordinal()).sample(inside, min(size, len(inside)))
    return tuple(CallSampleLine(patient) for patient in sorted(set(picked)))


def flag_event_ids(events: Sequence[Any]) -> dict[str, str]:
    """The latest `delivery_flagged` record of each parcel: the id a manager's decision points at."""
    ids: dict[str, str] = {}
    for event in events:
        if event.type == EventType.DELIVERY_FLAGGED.value:
            ids[str(event.payload.get("delivery_id"))] = event.event_id
    return ids


__all__ = [
    "CALL_SAMPLE_SIZE",
    "CallSampleLine",
    "EventCountLine",
    "OverdueFaultLine",
    "call_sample",
    "event_counts",
    "flag_event_ids",
    "overdue_faults",
]
