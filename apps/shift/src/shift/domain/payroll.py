"""The accountant's month as a payroll (S01 §5): one line per confirmation and deduction.

This module is split out of `rewards.py` to keep the rule file inside its line cap. It holds no rule
of its own: `Payroll` is the accountant's whole month, its lines carry the names and the Persian
amounts a payslip needs, and `per_line` is the only aggregation. `rewards.py` re-exports both names,
so every existing import keeps working.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PayrollLine:
    """One row of the accountant's list; a deduction line carries a negative amount."""

    staff_id: str
    staff_name: str
    work_type: str
    source: str
    amount_toman: int
    at_tehran: str
    note: str | None


@dataclass(frozen=True, slots=True)
class Payroll:
    """One month of the payroll: its lines, what it earned, what it takes back and the net."""

    month: str
    lines: tuple[PayrollLine, ...]
    earned_toman: int
    deducted_toman: int
    net_toman: int

    def per_staff(self) -> tuple[tuple[str, str, int], ...]:
        """`(staff_id, name, net)` per person, by name: what the accountant adds to a payslip, zero too."""
        names = {line.staff_id: line.staff_name for line in self.lines}
        nets: dict[str, int] = {}
        for line in self.lines:
            nets[line.staff_id] = nets.get(line.staff_id, 0) + line.amount_toman
        return tuple(sorted((staff_id, names[staff_id], net) for staff_id, net in nets.items()))
