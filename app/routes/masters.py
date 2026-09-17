"""The branch's lists and settings (Branch Console > Lists and Settings).

Each screen has its own access: Item lists, reasons, payment methods, and receipt and gift voucher settings. Reading is
wider than the screen, on purpose: Billing reads payment methods and what bills print, Adjustments and Returns read
their reasons, and none of them should break for want of a tick on the list's own screen.
"""
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.middlewares.auth import get_current_user, require_any_permission, require_permission
from app.models import ListEntry, PaymentMethod, User
from app.schemas.import_result import ImportRowError, ImportSummary
from app.schemas.masters import (
    BankAccountOut, ItemListChoicesOut, ListEntryCreate, ListEntryOut, ListEntryUpdate, ListSummaryOut, PaymentMethodOut,
    PaymentMethodsOut, PaymentMethodUpdate, PricingStockIn, PricingStockOut, ReasonCreate, ReasonUpdate, ReceiptSettingsIn,
    ReceiptSettingsOut, VoucherRulesIn, VoucherRulesOut,
)
from app.services import masters_service as svc
from app.services.import_service import parse_rows

router = APIRouter(prefix="/masters", tags=["lists and settings"])

_lists_read = require_any_permission(("branch-console.item-lists", "R"), ("inventory.catalog", "R"), ("branch-console.customers", "R"))
_lists_screen = require_permission("branch-console.item-lists", "R")
_lists_write = require_permission("branch-console.item-lists", "W")
_reasons_read = require_any_permission(
    ("branch-console.reasons", "R"), ("inventory.adjustments", "R"), ("inventory.purchase-returns", "R"), ("store.returns", "R"),
    ("branch-console.approvals", "R"), ("inventory.movements", "R"), ("reports", "R"),
)
_reasons_write = require_permission("branch-console.reasons", "W")
_methods_read = require_any_permission(
    ("branch-console.payment-methods", "R"), ("store.billing", "R"), ("store.returns", "R"), ("store.gift-vouchers", "R"),
    ("accounts.receivables", "R"),
)
_methods_write = require_permission("branch-console.payment-methods", "W")
_receipt_read = require_any_permission(("branch-console.shop-settings", "R"), ("store.billing", "R"), ("store.returns", "R"))
_vouchers_read = require_any_permission(("branch-console.shop-settings", "R"), ("store.gift-vouchers", "R"))
_settings_write = require_permission("branch-console.shop-settings", "W")


def _fail(exc: svc.MastersError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def _entry_out(entry: ListEntry, uses: int = 0, moved: int | None = None) -> ListEntryOut:
    return ListEntryOut(
        id=str(entry.id), kind=entry.kind, code=entry.code, name=entry.name, active=entry.active, builtin=entry.builtin,
        effect=entry.effect, uses=uses, moved=moved, updatedAt=entry.updated_at, updatedBy=entry.updated_by_name,
    )


# ── Item lists ──────────────────────────────────────────────────────────────────────────────────

@router.get("/item-lists/summary", response_model=list[ListSummaryOut])
async def item_list_summary(user: User = Depends(_lists_screen)) -> list[ListSummaryOut]:
    return [ListSummaryOut(**row) for row in await svc.item_list_summary()]


@router.get("/item-lists/choices", response_model=ItemListChoicesOut)
async def item_list_choices(user: User = Depends(_lists_read)) -> ItemListChoicesOut:
    """Switched-on values of every list, for the Item and Party forms."""
    return ItemListChoicesOut(choices=await svc.choices())


@router.get("/item-lists", response_model=list[ListEntryOut])
async def item_list(kind: str = Query(...), user: User = Depends(_lists_screen)) -> list[ListEntryOut]:
    """One list, switched-off entries included, with how many Items (or customers) use each."""
    try:
        return [_entry_out(entry, uses) for entry, uses in await svc.item_list(kind)]
    except svc.MastersError as exc:
        raise _fail(exc)


@router.post("/item-lists", response_model=ListEntryOut)
async def add_item_entry(payload: ListEntryCreate, user: User = Depends(_lists_write)) -> ListEntryOut:
    try:
        return _entry_out(await svc.add_item_entry(payload.kind, payload.name, user))
    except svc.MastersError as exc:
        raise _fail(exc)


@router.patch("/item-lists/{entry_id}", response_model=ListEntryOut)
async def update_item_entry(entry_id: str, payload: ListEntryUpdate, user: User = Depends(_lists_write)) -> ListEntryOut:
    """Rename (every Item or customer using it changes too), merge into an entry already on the list (`merge`), or
    switch on or off. Switched-off values stay on the records; the forms stop offering them."""
    try:
        entry, moved, uses = await svc.update_item_entry(entry_id, payload.name, payload.active, payload.merge, user)
    except svc.MastersError as exc:
        raise _fail(exc)
    return _entry_out(entry, uses, moved)


@router.delete("/item-lists/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item_entry(entry_id: str, user: User = Depends(_lists_write)) -> None:
    """Only an entry nothing uses (a typing mistake). Anything in use is switched off instead."""
    try:
        await svc.delete_item_entry(entry_id)
    except svc.MastersError as exc:
        raise _fail(exc)


@router.post("/item-lists/import", response_model=ImportSummary)
async def import_item_lists(file: UploadFile = File(...), user: User = Depends(_lists_write)) -> ImportSummary:
    """Columns List, Name and optionally Active (yes or no). Adds what's missing and switches entries on or off."""
    rows = parse_rows(file.filename, await file.read())
    created, updated, errors = await svc.import_item_lists(rows, user)
    return ImportSummary(created=created, updated=updated, errors=[ImportRowError(row=r, message=m) for r, m in errors])


# ── Reasons ─────────────────────────────────────────────────────────────────────────────────────

@router.get("/reasons", response_model=list[ListEntryOut])
async def reasons(kind: str | None = None, user: User = Depends(_reasons_read)) -> list[ListEntryOut]:
    """Reasons for stock adjustments, returns to suppliers and customer returns, switched-off ones included (records
    still need their names)."""
    try:
        return [_entry_out(entry, uses) for entry, uses in await svc.reasons(kind)]
    except svc.MastersError as exc:
        raise _fail(exc)


@router.post("/reasons", response_model=ListEntryOut)
async def add_reason(payload: ReasonCreate, user: User = Depends(_reasons_write)) -> ListEntryOut:
    try:
        return _entry_out(await svc.add_reason(payload.kind, payload.name, payload.effect, user))
    except svc.MastersError as exc:
        raise _fail(exc)


@router.patch("/reasons/{entry_id}", response_model=ListEntryOut)
async def update_reason(entry_id: str, payload: ReasonUpdate, user: User = Depends(_reasons_write)) -> ListEntryOut:
    try:
        entry, uses = await svc.update_reason(entry_id, payload.name, payload.active, payload.effect, user)
    except svc.MastersError as exc:
        raise _fail(exc)
    return _entry_out(entry, uses)


@router.delete("/reasons/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reason(entry_id: str, user: User = Depends(_reasons_write)) -> None:
    try:
        await svc.delete_reason(entry_id)
    except svc.MastersError as exc:
        raise _fail(exc)


# ── Payment methods ─────────────────────────────────────────────────────────────────────────────

def _method_out(m: PaymentMethod) -> PaymentMethodOut:
    return PaymentMethodOut(
        code=m.code, name=m.name, kind=m.kind, active=m.active, sortOrder=m.sort_order,
        rules=svc.METHOD_RULES.get(m.kind, ""), canSwitchOff=m.code != "CASH",
    )


async def _methods_payload() -> PaymentMethodsOut:
    return PaymentMethodsOut(
        methods=[_method_out(m) for m in await svc.payment_methods()],
        bankAccounts=[
            BankAccountOut(id=str(a.id), name=a.name, bankName=a.bank_name, accountNo=a.bank_account_no)
            for a in await svc.bank_accounts()
        ],
    )


@router.get("/payment-methods", response_model=PaymentMethodsOut)
async def payment_methods(user: User = Depends(_methods_read)) -> PaymentMethodsOut:
    """Every payment method with whether it's switched on here, and the shop bank accounts a transfer can go into."""
    return await _methods_payload()


@router.patch("/payment-methods/{code}", response_model=PaymentMethodsOut)
async def update_payment_method(code: str, payload: PaymentMethodUpdate, user: User = Depends(_methods_write)) -> PaymentMethodsOut:
    try:
        await svc.update_payment_method(code, payload.name, payload.active, payload.sortOrder)
    except svc.MastersError as exc:
        raise _fail(exc)
    return await _methods_payload()


# ── Receipt and gift voucher settings ───────────────────────────────────────────────────────────

async def _receipt_out() -> ReceiptSettingsOut:
    from app.services import registration_service

    value, row = await svc.get_setting(svc.RECEIPT_KEY)
    identity = await registration_service.current()
    address = None
    if identity:
        address = ", ".join(part for part in (identity.address, identity.city) if part) or None
    return ReceiptSettingsOut(
        **value, branchName=identity.name if identity else None, branchAddress=address, branchPhone=identity.phone if identity else None,
        updatedAt=row.updated_at if row else None, updatedBy=row.updated_by_name if row else None,
    )


@router.get("/settings/receipt", response_model=ReceiptSettingsOut)
async def receipt_settings(user: User = Depends(_receipt_read)) -> ReceiptSettingsOut:
    """What every bill prints besides the sale: the business name, NTN and STRN, a note, the footer and the return
    policy. The branch's own name, address and phone come from its head office registration."""
    return await _receipt_out()


@router.put("/settings/receipt", response_model=ReceiptSettingsOut)
async def save_receipt_settings(payload: ReceiptSettingsIn, user: User = Depends(_settings_write)) -> ReceiptSettingsOut:
    value = {k: ((v or "").strip() or None) for k, v in payload.model_dump().items()}
    await svc.put_setting(svc.RECEIPT_KEY, value, user)
    return await _receipt_out()


async def _vouchers_out() -> VoucherRulesOut:
    rules = await svc.voucher_rules()
    _, row = await svc.get_setting(svc.VOUCHER_KEY)
    return VoucherRulesOut(**rules, updatedAt=row.updated_at if row else None, updatedBy=row.updated_by_name if row else None)


@router.get("/settings/gift-vouchers", response_model=VoucherRulesOut)
async def voucher_rules(user: User = Depends(_vouchers_read)) -> VoucherRulesOut:
    return await _vouchers_out()


@router.put("/settings/gift-vouchers", response_model=VoucherRulesOut)
async def save_voucher_rules(payload: VoucherRulesIn, user: User = Depends(_settings_write)) -> VoucherRulesOut:
    """Applies to vouchers issued from now on. A voucher already issued keeps the expiry it was given."""
    if payload.maxValue is not None and payload.maxValue < payload.minValue:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The highest value can't be below the lowest.")
    value = {
        "validityDays": payload.validityDays, "minValue": format(payload.minValue, "f"),
        "maxValue": format(payload.maxValue, "f") if payload.maxValue is not None else None,
    }
    await svc.put_setting(svc.VOUCHER_KEY, value, user)
    return await _vouchers_out()


# ── Wholesale discount and the usual low stock level ────────────────────────────────────────────

async def _pricing_stock_out() -> PricingStockOut:
    values = await svc.pricing_stock()
    _, row = await svc.get_setting(svc.PRICING_STOCK_KEY)
    return PricingStockOut(**values, updatedAt=row.updated_at if row else None, updatedBy=row.updated_by_name if row else None)


# Read by anyone signed in: Billing prices wholesale bills with it, and the dashboard, Stock Overview and reports flag
# low stock with it. Two numbers, nothing private in them.
@router.get("/settings/pricing-stock", response_model=PricingStockOut)
async def pricing_stock_settings(user: User = Depends(get_current_user)) -> PricingStockOut:
    return await _pricing_stock_out()


@router.put("/settings/pricing-stock", response_model=PricingStockOut)
async def save_pricing_stock_settings(payload: PricingStockIn, user: User = Depends(_settings_write)) -> PricingStockOut:
    """From the next bill and the next look at stock. Bills already made keep the prices they were rung at."""
    value = {"wholesaleDiscountPercent": format(payload.wholesaleDiscountPercent.normalize(), "f"),
             "lowStockLevel": format(payload.lowStockLevel.normalize(), "f")}
    await svc.put_setting(svc.PRICING_STOCK_KEY, value, user)
    return await _pricing_stock_out()


@router.on_event("startup")
async def _ready_the_lists() -> None:
    """Built-in and starter reasons exist from the first start, so the screens that pick them never open empty.
    Started from this router so it needs no line in app/main.py."""
    try:
        await svc.ensure_reasons()
    except Exception:  # noqa: BLE001 - a database not yet upgraded; the first request tries again
        svc._reasons_ready = False
