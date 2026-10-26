"""Who pays what on an invoice (ACC-2.2). The clinic's money rules as pure functions.

`InvoiceRepository.get_invoice_items` used to hold these rules inline between SQL queries. They are
pinned by `tests/test_billing_characterization.py` (golden-master of the *current* behaviour) and by
`tests/test_domain.py`; changing a rule here changes money and needs the owner's decision.

Each share function returns `Shares(recorded, patient, insurance, covered)`:
`recorded` is the full price, `patient` what the patient pays, `insurance` what the insurer owes.
Consumables are always paid by the patient and are not revenue (owner decision, Q-05).
"""

from __future__ import annotations

from dataclasses import dataclass

PAYMENT_TYPES = ("card", "cash")


@dataclass(frozen=True)
class Shares:
    recorded: float
    patient: float
    insurance: float
    covered: int  # 1 when insurance covers the whole item

    def as_item_fields(self) -> dict:
        return {
            "recorded_price": float(self.recorded),
            "patient_share": float(self.patient),
            "insurance_share": float(self.insurance),
            "covered_by_insurance": self.covered,
        }


def nursing_is_covered(nursing_covers: bool | None, nursing_tariff: float | None) -> bool:
    """Does this insurance cover nursing? The manager's checkbox wins; the legacy numeric tariff
    (0 meant 'free for the patient') applies only when the checkbox was never set."""
    if nursing_covers is not None:
        return bool(nursing_covers)
    return nursing_tariff is not None and float(nursing_tariff) == 0


def visit_shares(base_price: float, insurance_price: float | None, supplementary_price: float | None) -> Shares:
    """Visit: the recorded price is the base (free-pay) tariff; the patient pays the insurance tariff,
    replaced by the supplementary-insurance tariff when there is one."""
    recorded = float(base_price)
    patient = recorded
    if insurance_price is not None:
        patient = float(insurance_price)
    if supplementary_price is not None:
        patient = float(supplementary_price)
    insurance = recorded - patient if recorded > patient else 0
    covered = 1 if patient == 0 and recorded > 0 else 0
    return Shares(recorded, patient, float(insurance), covered)


def injection_shares(price: float, nursing_covered: bool, excluded: bool) -> Shares:
    """Nursing service: fully covered when the insurance covers nursing, unless this service is on its
    exclusion list (then the patient pays everything)."""
    price = float(price or 0)
    if nursing_covered and not excluded:
        return Shares(price, 0.0, price, 1)
    return Shares(price, price, 0.0, 0)


def procedure_shares(price: float, performed_by_nurse: bool, nursing_covered: bool) -> Shares:
    """Procedure: only nurse-performed procedures follow the nursing coverage; a doctor's procedure is
    always paid by the patient."""
    price = float(price or 0)
    if performed_by_nurse and nursing_covered:
        return Shares(price, 0.0, price, 1)
    return Shares(price, price, 0.0, 0)


def consumable_shares(price: float) -> Shares:
    price = float(price or 0)
    return Shares(price, price, 0.0, 0)


def needs_payment_type(patient_share: float | None) -> bool:
    """An item the patient must pay for has to say how (card or cash). An item the insurance covers
    entirely has no payment to classify (ACC-2.9)."""
    return float(patient_share or 0) > 0


def is_valid_payment_type(payment_type: str | None) -> bool:
    return payment_type in PAYMENT_TYPES


def group_items(items: list[dict], payments: list[dict] | None = None) -> list[dict]:
    """Identical invoice lines shown once with ×N (FB-09).

    Lines are identical when type, title, full price and patient share match. Returns the same
    dicts, reordered so each group is contiguous (groups keep the order of their first line), with
    `group_id`, `group_size`, `group_first`; the first line of a group also carries the group's
    `group_recorded_total`, `group_share_total` and `group_paid` (how many lines are settled).
    """
    paid = {(p["item_type"], p["item_id"]) for p in (payments or []) if p.get("is_paid") == 1}
    groups: dict[tuple, list[dict]] = {}
    for it in items:
        key = (
            it.get("type"),
            it.get("description"),
            float(it.get("recorded_price") or 0),
            float(it.get("patient_share") or 0),
        )
        groups.setdefault(key, []).append(it)
    ordered = []
    for n, members in enumerate(groups.values()):
        for i, it in enumerate(members):
            it["group_id"] = f"g{n}"
            it["group_size"] = len(members)
            it["group_first"] = i == 0
        head = members[0]
        head["group_recorded_total"] = sum(float(m.get("recorded_price") or 0) for m in members)
        head["group_share_total"] = sum(float(m.get("patient_share") or 0) for m in members)
        head["group_paid"] = sum(1 for m in members if (m.get("type"), m.get("id")) in paid)
        ordered.extend(members)
    return ordered
