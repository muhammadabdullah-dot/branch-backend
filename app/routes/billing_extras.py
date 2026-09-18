"""Billing's own lists and settings: Sold without stock, Priced below cost, which departments are Pharmacy, scan
history, and recalling a held bill with the other side's lines cleared for payment. Pharmacy slips are routes/slips.py.

Included by routes/sales.py, so it needs no line in app/main.py.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.middlewares.auth import get_current_user, require_any_permission, require_permission
from app.models import User
from app.schemas.billing_extras import (
    PharmacySettingIn, PharmacySettingOut, PricedBelowCostOut, ScanEventsIn, ScanHistoryOut, SoldWithoutStockOut,
)
from app.schemas.held_bills import HeldBillRecallIn, HeldBillRecallOut
from app.services import pharmacy_service, price_checks_service, scan_history_service, stock_guard

router = APIRouter(tags=["billing extras"])

# Whoever looks after stock sees what was sold past zero; so does whoever may sell past zero.
_sold_without_stock_read = require_any_permission(
    ("inventory.overview", "R"), ("inventory.below-cost", "R"), ("store.sell-past-zero", "R"), ("branch-console.dashboard", "R"),
)
_below_cost_read = require_any_permission(("inventory.below-cost", "R"))


@router.get("/inventory/sold-without-stock", response_model=list[SoldWithoutStockOut])
async def sold_without_stock(user: User = Depends(_sold_without_stock_read)) -> list[SoldWithoutStockOut]:
    """Items Main Store is below zero on, because someone sold them past zero. Each leaves the list by itself once Main
    Store is back to zero or above (a delivery received into it)."""
    return [SoldWithoutStockOut(**row) for row in await stock_guard.sold_without_stock()]


@router.get("/inventory/priced-below-cost", response_model=PricedBelowCostOut)
async def priced_below_cost(user: User = Depends(_below_cost_read)) -> PricedBelowCostOut:
    """Items in stock whose own price is at or below their cost with tax (they sell with no discount), and Items in stock
    with no cost recorded."""
    return PricedBelowCostOut(**await price_checks_service.priced_below_cost())


# ── Which departments are Pharmacy ─────────────────────────────────────────────────────────────

_setting_write = require_permission("branch-console.shop-settings", "W")


async def _pharmacy_out() -> PharmacySettingOut:
    departments, row = await pharmacy_service.setting()
    return PharmacySettingOut(departments=departments, updatedAt=row.updated_at if row else None, updatedBy=row.updated_by_name if row else None)


# Read by anyone signed in: Billing keeps each side's Items off the other side's till.
@router.get("/masters/settings/pharmacy", response_model=PharmacySettingOut)
async def pharmacy_setting(user: User = Depends(get_current_user)) -> PharmacySettingOut:
    return await _pharmacy_out()


@router.put("/masters/settings/pharmacy", response_model=PharmacySettingOut)
async def save_pharmacy_setting(payload: PharmacySettingIn, user: User = Depends(_setting_write)) -> PharmacySettingOut:
    """From the next scan. Stock screens are unchanged; only who puts these Items on a bill: Pharmacists these only,
    Salespeople everything else."""
    await pharmacy_service.save_setting(payload.departments, user)
    return await _pharmacy_out()


# ── Recalling a held bill ─────────────────────────────────────────────────────────────────────

@router.post("/held-bills/{bill_id}/recall", response_model=HeldBillRecallOut)
async def recall_held_bill(
    bill_id: str, payload: HeldBillRecallIn, user: User = Depends(require_permission("store.hold-recall", "W")),
) -> HeldBillRecallOut:
    """Takes a held bill off the held list and hands it to the till. Someone recalling a bill that carries cleared lines
    they may not sell themselves gets a pass for those lines, so they can still take its payment. A held bill carrying
    Pharmacy Items isn't a Salesperson's to recall: they never see those Items (services/held_bills_service.py)."""
    from app.controllers.held_bills_controller import _to_out
    from app.services import held_bills_service

    held = await held_bills_service.get_for(user, bill_id)
    if not held:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That held bill is no longer there. It was recalled or voided at another counter.")
    out = _to_out(held)
    token = None
    cleared = await pharmacy_service.cleared_lines(held.lines or [], user)
    if cleared:
        token = pharmacy_service.issue_pass(user, payload.billId, cleared)
    await held.delete()
    return HeldBillRecallOut(bill=out, pharmacyPass=token)


# ── Scan history ──────────────────────────────────────────────────────────────────────────────

# Whoever can open Billing sends what happened on their bill lines; the report is its own tick.
@router.post("/sales/scan-events")
async def scan_events(payload: ScanEventsIn, user: User = Depends(require_permission("store.billing", "R"))) -> dict:
    """A batch from the till's queue. Sending the same events again keeps them once."""
    return {"saved": await scan_history_service.record(user, payload.events)}


@router.get("/reports/scan-history", response_model=ScanHistoryOut)
async def scan_history(
    day: date | None = None, userId: str | None = None, show: str = Query("removed", pattern="^(removed|unpaid|held|sold|all)$"),
    user: User = Depends(require_permission("reports.scan-history", "R")),
) -> ScanHistoryOut:
    """One Pakistan day's bill lines (today when no day is given): by default those taken off a bill or never paid."""
    from app.core.pk_time import today_pk

    return ScanHistoryOut(**await scan_history_service.report(day or today_pk(), userId, show, user))


@router.on_event("startup")
async def _sell_loose_from_legacy_pack_size() -> None:
    """Once, and harmless again: legacy Items whose pack size was the pieces inside the stocked unit (no pack unit) sell
    loose by that many pieces (services/sell_levels.py). Started from this router so it needs no line in app/main.py."""
    from app.services import sell_levels

    try:
        count = await sell_levels.backfill_loose()
    except Exception:  # noqa: BLE001 - a database not yet upgraded: the next start tries again
        return
    if count:
        print(f"  {count} Item(s) can now be sold loose, by the piece", flush=True)
