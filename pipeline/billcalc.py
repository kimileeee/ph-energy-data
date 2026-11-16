"""The bill formula: from a utility's unbundled rate schedule to an effective rate in PHP/kWh.

Philippine retail bills are unbundled (EPIRA, RA 9136, and ERC's rules on the uniform bill format). For a customer
using Q kWh in a month:

    bill(Q) = Q * sum(per-kWh charges)          generation, transmission, ancillary, system loss, distribution,
                                                supply, metering, lifeline/senior subsidies, universal charges,
                                                FIT-All, GEA-All, RPT, ...
            + sum(per-customer charges)         supply and metering charges per customer per month
            + local franchise tax               a percentage of the base charges (CELCOR prints 0.825%)
            + VAT                               12% on distribution-side charges; on generation and transmission,
                                                the VAT the suppliers actually billed (it varies by supplier mix),
                                                so it can't be rebuilt from the schedule alone
            - lifeline / senior discounts       only for qualified customers at low consumption

    effective rate(Q) = bill(Q) / Q

There is no single "true rate": the fixed per-customer charges are spread over Q, so the rate falls as Q rises.
The convention (Meralco's published typical bills; the figures DOE copies for Meralco) is Q = 200 kWh.

Verified against CELCOR's October 2026 documents: its schedule's per-kWh charges x 200 plus its per-customer charges
reproduce every non-tax line of its published 200 kWh bill to the centavo, and its franchise tax is exactly 0.825% of
their sum (test in test_pipeline.py).
"""

TYPICAL_KWH = 200
EXCLUDE = {"effective_rate", "total_bill", "taxes"}  # all-in figures, or taxes we can't rebuild


def bill(per_kwh: dict[str, float], per_customer: dict[str, float], kwh: float = TYPICAL_KWH,
         franchise_tax_pct: float | None = None, vat: float | None = None) -> dict:
    """Line items in pesos at `kwh`, the subtotal before tax, and the effective rate with whatever taxes are known."""
    items = {k: round(v * kwh, 2) for k, v in per_kwh.items()} | {k: round(v, 2) for k, v in per_customer.items()}
    subtotal = round(sum(items.values()), 2)
    lft = round(subtotal * franchise_tax_pct / 100, 2) if franchise_tax_pct is not None else None
    total = round(subtotal + (lft or 0) + (vat or 0), 2)
    return {"kwh": kwh, "items": items, "subtotal": subtotal, "franchise_tax": lft, "vat": vat, "total": total,
            "rate_before_tax": round(subtotal / kwh, 4), "rate": round(total / kwh, 4),
            "taxes_included": [t for t, v in (("franchise tax", lft), ("VAT", vat)) if v is not None]}


def from_facts(facts: list[dict], period: str, customer_class: str = "residential", kwh: float = TYPICAL_KWH) -> dict | None:
    """Build the bill from extracted schedule facts (pipeline/extract.py): the utility's own per-kWh and per-customer
    charges for one class and month. None when the schedule lacks a generation charge (not a usable schedule)."""
    mine = [f for f in facts if f["period"] == period and f["kind"] == "level" and f.get("is_overall", True)
            and f["customer_class"] in (customer_class, "all") and f["measure"] not in EXCLUDE
            and not (f.get("subclass") or "").strip()]
    if not any(f["measure"] == "generation_charge" and f["unit"] == "PHP/kWh" for f in mine):
        return None
    label = lambda f: f["label_as_printed"].strip()
    per_kwh = {label(f): f["value"] for f in mine if f["unit"] == "PHP/kWh"}
    per_customer = {label(f): f["value"] for f in mine if f["unit"] == "PHP/customer/month"}
    return bill(per_kwh, per_customer, kwh)
