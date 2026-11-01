"""The owner's two reference formulas as an oracle for tests (H-01, H-02).

The app never computes a reference from these: the stock and cash projections hold it, and every event
carries its own sign. Tests use this to check that what the projection holds is what the formula says
when it is fed the real event log (`test_purchase_chain.py`).
"""


def shelf_reference(last_count: int, restocked: int, consumed: int, emergency: int, waste: int) -> int:
    """E = C0 + R - D - U - W (H-01): what the shelf should hold when the handover starts."""
    reference = last_count + restocked - consumed - emergency - waste
    if reference < 0:
        raise ValueError("shelf reference would be negative; check the shift's counts")
    return reference


def cash_reference(opening: int, receipts: int, withdrawals: int, deposits: int) -> int:
    """N = N0 + P_cash - B - W (H-02): what the cash box should hold when the handover starts."""
    reference = opening + receipts - withdrawals - deposits
    if reference < 0:
        raise ValueError("cash reference would be negative; check the shift's cash events")
    return reference
