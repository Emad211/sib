"""SQL for the reward rate table, the accounting identity map and the reward records (S01 §5).

The house style of `campaign_repo`: SQL only, one row type per read, no rules and no Persian text.
Three reads serve the whole feature. `reward_rates` answers what a work was worth in a month.
`vital_works` and `registration_works` are the accounting mirrors joined to `staff.acc_user` — a
`LEFT JOIN`, so an accounting user the manager has not mapped comes back with no staff row instead
of disappearing (decision 4: an unmapped earner earns nothing and is listed for him). `timeline` is
the reward half of the append-only log, one query, so the ledger is derived on every read and never
stored as a projection.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

from shift.adapters.db import mirror_repo

RATE_COLUMNS = ("work_type", "month", "amount_toman", "set_by", "set_at")
_RATE_SELECT = f"SELECT {', '.join(RATE_COLUMNS)} FROM reward_rates"
_UPDATED = RATE_COLUMNS[2:-1]

VITALS = "vitals"
REGISTRATION = "registration"
REWARD_EARNED = "reward.earned"
REWARD_CONFIRMED = "reward.confirmed"
REWARD_REVOKED = "reward.revoked"
DECISIONS = ("event.approved", "event.rejected")
REWARD_TYPES = (REWARD_EARNED, REWARD_CONFIRMED, REWARD_REVOKED)
_TIMELINE_TYPES = (*REWARD_TYPES, *DECISIONS)

# A work is one row of an accounting mirror: its own identity, its account, and when it happened.
_VITAL_WORKS = (
    "SELECT v.injection_id AS source_ref, v.closed_at AS done_at, CAST(v.nurse_id AS TEXT) AS acc_user, "
    "s.staff_id AS staff_id, s.full_name AS staff_name "
    f"FROM {mirror_repo.VITAL} AS v "
    "LEFT JOIN staff AS s ON s.acc_user = CAST(v.nurse_id AS TEXT) "
    "ORDER BY v.closed_at, v.injection_id"
)
# The registration reward needs both checks the owner asks for; a foreign patient with both counts.
# One mobile earns once (owner decision 5), so only the first registration for a mobile is a work;
# accounting answers which one that is, and a mirror built from _v1 marks every row as the first.
_REGISTRATION_WORKS = (
    "SELECT r.patient_id AS source_ref, r.created_at AS done_at, r.created_by AS acc_user, "
    "s.staff_id AS staff_id, s.full_name AS staff_name "
    f"FROM {mirror_repo.REGISTRATION} AS r "
    "LEFT JOIN staff AS s ON s.acc_user = r.created_by "
    "WHERE r.has_valid_nid = 1 AND r.has_valid_phone = 1 AND r.first_for_phone = 1 "
    "ORDER BY r.created_at, r.patient_id"
)
_TIMELINE = (
    "SELECT event_id, type, ref_event_id, at_tehran, payload FROM events "
    f"WHERE type IN ({', '.join('?' for _ in _TIMELINE_TYPES)}) ORDER BY seq"
)
_NAMES = "SELECT staff_id, full_name FROM staff"


@dataclass(frozen=True, slots=True)
class RewardRateRow:
    """One stored rate: the amount of one work type in one month, and who set it when."""

    work_type: str
    month: str
    amount_toman: int
    set_by: str
    set_at: str


@dataclass(frozen=True, slots=True)
class TeamGoalRow:
    """One month's team goal: the number the manager set and who set it when."""

    month: str
    goal_works: int
    set_by: str
    set_at: str


@dataclass(frozen=True, slots=True)
class WorkRow:
    """One work read from an accounting mirror, with the staff row its account maps to (if any)."""

    source: str
    source_ref: str
    done_at: str
    acc_user: str | None
    staff_id: str | None
    staff_name: str | None


@dataclass(frozen=True, slots=True)
class RewardEventRow:
    """One reward record (or one decision about one) as stored: its type, its payload and its time."""

    event_id: str
    type: str
    ref_event_id: str | None
    at_tehran: str
    payload: Mapping[str, Any]


def _rate(row: Mapping[str, Any]) -> RewardRateRow:
    return RewardRateRow(
        work_type=str(row["work_type"]),
        month=str(row["month"]),
        amount_toman=int(row["amount_toman"]),
        set_by=str(row["set_by"]),
        set_at=str(row["set_at"]),
    )


def _work(row: Mapping[str, Any], source: str) -> WorkRow:
    staff_id = row["staff_id"]
    return WorkRow(
        source=source,
        source_ref=str(row["source_ref"]),
        done_at=str(row["done_at"]),
        acc_user=None if row["acc_user"] is None else str(row["acc_user"]),
        staff_id=None if staff_id is None else str(staff_id),
        staff_name=None if row["staff_name"] is None else str(row["staff_name"]),
    )


def rates(connection: Connection, month: str) -> list[RewardRateRow]:
    """Every rate set for one month, by work type."""
    rows = connection.exec_driver_sql(f"{_RATE_SELECT} WHERE month = ? ORDER BY work_type", (month,)).mappings()
    return [_rate(row) for row in rows]


def rate(connection: Connection, work_type: str, month: str) -> RewardRateRow | None:
    """The rate of one work type in one month, or None while the manager has not set it."""
    row = (
        connection.exec_driver_sql(f"{_RATE_SELECT} WHERE work_type = ? AND month = ?", (work_type, month))
        .mappings()
        .first()
    )
    return None if row is None else _rate(row)


def upsert_rate(connection: Connection, values: Mapping[str, Any]) -> RewardRateRow:
    """Store one month's rate, replacing an earlier amount for the same work type and month."""
    updates = ", ".join(f"{column} = excluded.{column}" for column in _UPDATED)
    placeholders = ", ".join("?" for _ in RATE_COLUMNS)
    row = (
        connection.exec_driver_sql(
            f"INSERT INTO reward_rates ({', '.join(RATE_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT (work_type, month) DO UPDATE SET {updates} "
            f"RETURNING {', '.join(RATE_COLUMNS)}",
            tuple(values[column] for column in RATE_COLUMNS),
        )
        .mappings()
        .one()
    )
    return _rate(row)


def team_goal(connection: Connection, month: str) -> TeamGoalRow | None:
    """The team goal of one month, or None while the manager has not set one."""
    row = (
        connection.exec_driver_sql(
            "SELECT month, goal_works, set_by, set_at FROM reward_team_goals WHERE month = ?", (month,)
        )
        .mappings()
        .first()
    )
    return (
        None
        if row is None
        else TeamGoalRow(
            month=str(row["month"]),
            goal_works=int(row["goal_works"]),
            set_by=str(row["set_by"]),
            set_at=str(row["set_at"]),
        )
    )


def set_team_goal(connection: Connection, values: Mapping[str, Any]) -> TeamGoalRow:
    """Store one month's team goal, replacing an earlier number for the same month."""
    row = (
        connection.exec_driver_sql(
            "INSERT INTO reward_team_goals (month, goal_works, set_by, set_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (month) DO UPDATE SET goal_works = excluded.goal_works, "
            "set_by = excluded.set_by, set_at = excluded.set_at "
            "RETURNING month, goal_works, set_by, set_at",
            (values["month"], values["goal_works"], values["set_by"], values["set_at"]),
        )
        .mappings()
        .one()
    )
    return TeamGoalRow(
        month=str(row["month"]),
        goal_works=int(row["goal_works"]),
        set_by=str(row["set_by"]),
        set_at=str(row["set_at"]),
    )


def acc_users(connection: Connection) -> dict[str, str | None]:
    """The accounting user each staff row is mapped to, by staff id (None while unmapped)."""
    rows = connection.exec_driver_sql("SELECT staff_id, acc_user FROM staff").mappings()
    return {str(row["staff_id"]): None if row["acc_user"] is None else str(row["acc_user"]) for row in rows}


def acc_user_owner(connection: Connection, acc_user: str) -> str | None:
    """The staff row that already claims one accounting user, so a second one can be refused."""
    row = connection.exec_driver_sql("SELECT staff_id FROM staff WHERE acc_user = ?", (acc_user,)).first()
    return None if row is None else str(row[0])


def set_acc_user(connection: Connection, staff_id: str, acc_user: str | None) -> None:
    """Map (or unmap, with None) one staff row to the accounting user that did the work."""
    connection.exec_driver_sql("UPDATE staff SET acc_user = ? WHERE staff_id = ?", (acc_user, staff_id))


def vital_works(connection: Connection) -> list[WorkRow]:
    """Every sugar/BP service accounting recorded, with its nurse's staff row when it is mapped."""
    return [_work(row, VITALS) for row in connection.exec_driver_sql(_VITAL_WORKS).mappings()]


def registration_works(connection: Connection) -> list[WorkRow]:
    """Every registration with a valid national ID and a valid mobile, mapped to its staff row."""
    return [_work(row, REGISTRATION) for row in connection.exec_driver_sql(_REGISTRATION_WORKS).mappings()]


def names(connection: Connection) -> dict[str, str]:
    """Every staff name, by staff id: the ledger prints a name, and an id for someone who left."""
    return {str(row[0]): str(row[1]) for row in connection.exec_driver_sql(_NAMES)}


def timeline(connection: Connection) -> list[RewardEventRow]:
    """The reward records and their decisions, oldest first: the whole input of the ledger."""
    return [
        RewardEventRow(
            event_id=str(row["event_id"]),
            type=str(row["type"]),
            ref_event_id=None if row["ref_event_id"] is None else str(row["ref_event_id"]),
            at_tehran=str(row["at_tehran"]),
            payload=json.loads(row["payload"]),
        )
        for row in connection.exec_driver_sql(_TIMELINE, _TIMELINE_TYPES).mappings()
    ]
