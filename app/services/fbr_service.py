"""FBR invoices for bills and returns: FBR's POS integration for Tier-1 retailers.

What FBR documents (read 21 September 2026; the notes, with every source, are in docs/FBR-POS-integration-notes.md):

- PRAL, "Technical Specification for Data Sharing through Software Fiscal Component from TIER 1 Retailer with FBR"
  (https://help.fbr.gov.pk/?p=6148): each POS terminal is registered on IRIS (https://e.fbr.gov.pk, Registration >
  POS Client Registration) and gets a POS ID; a cloud POS posts each invoice as JSON to PostData with the bearer token
  FBR issues, and gets FBR's invoice number back to print with a QR code of it.
- SRO 1006(I)/2021 (https://download1.fbr.gov.pk/SROs/202189168291729SRO-1006.pdf): what the receipt must print.
- Sales Tax Rules 2006, rule 150XC (SRO 69(I)/2025): invoices made while FBR can't be reached are clearly marked as
  issued offline and uploaded within 24 hours of the connection coming back.

How this branch issues them is the Branch Manager's setting (Branch Console > Lists and Settings > Receipt and Vouchers >
FBR invoices), one of four modes:

- off: bills and returns carry no FBR invoice, and receipts print no FBR block.
- dummy (the default until FBR registration is done): every bill and return gets a test number made here, never sent
  anywhere: TEST-<branch code>-<counter code>-<running number>, e.g. TEST-GG-C1-000001. The branch code keeps two
  branches apart and the counter code two counters; each counter counts from 1. The receipt prints it with its QR where
  the real one goes, and says plainly that it is a test number, not registered with FBR.
- sandbox and production: once the bill (or return) has committed, it is posted to FBR's sandbox or live PostData with
  the counter's POS ID and the branch's token, and FBR's number is kept and printed. When FBR can't be reached the sale
  still completes: the invoice waits (the receipt says "issued offline, waiting for FBR") and a loop in this server
  sends it again on its own until FBR takes it. Nothing here ever holds up the till.

Nothing here reaches head office: the settings table isn't synced, and the token is never logged or sent back whole.
"""
import asyncio
import contextlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import httpx

from app.core import logs
from app.core.pk_time import pk_time
from app.models import FbrInvoice, FbrSettings, ReturnLine, ReturnRecord, SaleLine, SaleRecord, SaleTender, SalesCounter, TillSession, User

MODES = ("off", "dummy", "sandbox", "production")
DEFAULT_MODE = "dummy"
LIVE_MODES = ("sandbox", "production")
MODE_LABELS = {"off": "Off", "dummy": "Dummy (test numbers)", "sandbox": "Sandbox (FBR test system)", "production": "Production"}

# FBR's PostData endpoints for a cloud or server POS, from the PRAL document above. FBR_SANDBOX_URL and FBR_PRODUCTION_URL
# point them elsewhere (a local stand-in while testing); nothing else should.
SANDBOX_URL = "https://esp.fbr.gov.pk:8244/FBR/v1/api/Live/PostData"
PRODUCTION_URL = "https://gw.fbr.gov.pk/imsp/v1/api/Live/PostData"
# How long the till waits for FBR right after a sale, and how long the loop waits.
INLINE_SECONDS = 4.0
LOOP_SECONDS = 10.0
# FBR is taken as down for this long after it last failed: the next bills queue at once instead of each waiting, and the
# loop only tries one invoice in that time.
DOWN_FOR_SECONDS = 60
# Waiting invoices are tried again after 30 s, then 1, 2, 4 and 8 minutes, then every 10 minutes.
RETRY_STEPS = (30, 60, 120, 240, 480)
RETRY_MAX = 600

# The test numbers: TEST-GG-C1-000001. The sale's own column holds 30 characters, so the parts are kept to fit.
DUMMY_PREFIX = "TEST"
NUMBER_WIDTH = 6

# FBR's PaymentMode (the PRAL document's list): 1 Cash, 2 Card, 3 Gift Voucher, 4 Loyalty Card, 5 Mixed, 6 Cheque.
# FBR's list has no wallet, bank transfer or customer account; those three are our choice, to confirm with PRAL.
PAYMENT_MODES = {"CASH": 1, "CARD": 2, "VOUCHER": 3, "POINTS": 4, "CHEQUE": 6, "EASYPAISA": 2, "JAZZCASH": 2, "BANK": 2, "CREDIT": 1}
MIXED = 5
# FBR's InvoiceType: 1 New, 2 Debit, 3 Credit (a return against an earlier invoice, naming it in RefUSIN).
NEW, CREDIT = 1, 3

CENT = Decimal("0.01")
ZERO = Decimal("0")
_POS_ID = re.compile(r"^\d{1,18}$")
_PCT = re.compile(r"^\d{4,8}$")


class FbrError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


# Settings:

async def _settings() -> FbrSettings:
    """The branch's FBR settings. Never created here (a bill's transaction could otherwise race another for the one
    row): until a Branch Manager saves them, the defaults stand."""
    row = await FbrSettings.get_or_none(id=1)
    return row if row is not None else FbrSettings(id=1, mode=DEFAULT_MODE, pos_ids={})


async def current_mode() -> str:
    mode = (await _settings()).mode
    return mode if mode in MODES else DEFAULT_MODE


def _endpoint(mode: str) -> str:
    if mode == "production":
        return os.environ.get("FBR_PRODUCTION_URL") or PRODUCTION_URL
    return os.environ.get("FBR_SANDBOX_URL") or SANDBOX_URL


def _token_hint(token: str | None) -> str | None:
    """Enough to tell which token is saved, never the token: its last four characters."""
    return f"ends in {token[-4:]}" if token and len(token) >= 8 else ("saved" if token else None)


async def settings_out(viewer: User | None) -> dict:
    from app.core.abilities import BRANCH_MANAGER

    row = await _settings()
    pos_ids = row.pos_ids if isinstance(row.pos_ids, dict) else {}
    counters = await SalesCounter.all().order_by("sort_order", "code")
    return {
        "mode": row.mode if row.mode in MODES else DEFAULT_MODE,
        "tokenSet": bool(row.access_token),
        "tokenHint": _token_hint(row.access_token),
        "counters": [
            {"id": str(c.id), "code": c.code, "name": c.name, "active": c.active, "posId": pos_ids.get(str(c.id))}
            for c in counters
        ],
        "taxOffice": row.tax_office,
        "defaultPctCode": row.default_pct_code,
        "sandboxUrl": _endpoint("sandbox"),
        "productionUrl": _endpoint("production"),
        "updatedAt": row.updated_at,
        "updatedBy": row.updated_by_name,
        "canChange": bool(viewer and viewer.role_id == BRANCH_MANAGER),
        "queue": await queue_summary(),
    }


async def save_settings(user: User, data) -> tuple[dict, str]:
    """Replaces the settings. `data.accessToken` left out keeps the saved token; `data.clearToken` removes it. Returns
    the settings as a screen sees them and a note of what changed for the activity trail (never the token itself)."""
    from app.core.abilities import BRANCH_MANAGER

    if user.role_id != BRANCH_MANAGER:
        raise FbrError("Only a Branch Manager can change the FBR settings.", 403)
    mode = (data.mode or "").strip().lower()
    if mode not in MODES:
        raise FbrError("Pick how bills get their FBR invoice: Off, Dummy, Sandbox or Production.")

    known = {str(c.id): c for c in await SalesCounter.all()}
    pos_ids: dict[str, str] = {}
    for counter_id, value in (data.posIds or {}).items():
        text = (value or "").strip()
        if not text:
            continue
        counter = known.get(str(counter_id))
        if counter is None:
            raise FbrError("One of the counters isn't at this branch any more. Refresh the page and try again.")
        if not _POS_ID.match(text):
            raise FbrError(f"The POS ID for {counter.code} is the number FBR gave that counter when it was registered on IRIS, digits only.")
        if text in pos_ids.values():
            raise FbrError(f"POS ID {text} is on two counters. FBR gives each counter its own.")
        pos_ids[str(counter_id)] = text

    row = await FbrSettings.get_or_none(id=1)
    token = row.access_token if row else None
    new_token = (data.accessToken or "").strip()
    token_note = None
    if data.clearToken:
        token, token_note = None, "token removed"
    elif new_token:
        if len(new_token) < 8 or any(ch.isspace() for ch in new_token):
            raise FbrError("That doesn't look like the access token FBR issued. Paste it again, without spaces.")
        token, token_note = new_token, "token replaced" if row and row.access_token else "token saved"

    tax_office = " ".join((data.taxOffice or "").split())[:80] or None
    pct = (data.defaultPctCode or "").strip() or None
    if pct and not _PCT.match(pct):
        raise FbrError("A PCT code is the customs tariff code of the goods: 4 to 8 digits, like 30049099.")

    if mode in LIVE_MODES:
        if not token:
            raise FbrError(f"Enter the access token FBR issued before switching to {MODE_LABELS[mode]}.")
        if not pos_ids:
            raise FbrError(f"Enter the POS ID FBR gave at least one counter before switching to {MODE_LABELS[mode]}.")
        if not pct:
            raise FbrError("Enter a PCT code for Items that don't have their own: FBR wants one on every line.")

    before = row.mode if row else DEFAULT_MODE
    now = datetime.now(timezone.utc)
    if row is None:
        row = await FbrSettings.create(
            id=1, mode=mode, access_token=token, pos_ids=pos_ids, tax_office=tax_office, default_pct_code=pct,
            updated_at=now, updated_by_name=user.name,
        )
    else:
        row.mode, row.access_token, row.pos_ids, row.tax_office, row.default_pct_code = mode, token, pos_ids, tax_office, pct
        row.updated_at, row.updated_by_name = now, user.name
        await row.save()

    changes = [f"mode {MODE_LABELS[mode]}" + (f" (was {MODE_LABELS.get(before, before)})" if before != mode else "")]
    codes = [known[cid].code for cid in pos_ids]
    changes.append(f"POS IDs for {', '.join(codes)}" if codes else "no POS IDs")
    if token_note:
        changes.append(token_note)
    return await settings_out(user), "FBR invoices: " + "; ".join(changes)


# The test numbers:

def _code_part(text: str | None, most: int) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())[: max(most, 0)]


async def _dummy_number(counter: SalesCounter | None) -> str:
    """TEST-GG-C1-000001: this branch's code, the counter's code, and that counter's own running number from 1. It
    carries letters, which no FBR invoice number does, so it can't be taken for one."""
    from app.services import numbering_service
    from app.services.sales_service import invoice_prefix

    branch = _code_part(await invoice_prefix(), 8) or "BR"
    # 30 characters in all: TEST, the branch, the counter, the number and three dashes.
    place = _code_part(counter.code if counter else None, 30 - len(DUMMY_PREFIX) - len(branch) - NUMBER_WIDTH - 3) or "TILL"
    prefix = f"{DUMMY_PREFIX}-{branch}-{place}-"
    return await numbering_service.next_number(f"fbr-test:{branch}:{place}", FbrInvoice, "fbr_invoice_number", prefix, NUMBER_WIDTH)


async def _counter_of(till_session_id) -> SalesCounter | None:
    if not till_session_id:
        return None
    session = await TillSession.get_or_none(id=till_session_id)
    if not session or not session.counter_id:
        return None
    return await SalesCounter.get_or_none(id=session.counter_id)


# What is sent. Field by field as the PRAL document's "Invoice Model Details" has them (https://help.fbr.gov.pk/?p=6148).
# Its own sample (1,298 sale value + 221 tax = 1,519 bill amount) reads SaleValue as the value tax is worked on: after
# the discount, before tax. Its .NET example disagrees with that sample; the table and the sample are followed here.

def _num(value) -> float:
    return float(Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP))


def _pct_code(product, settings: FbrSettings) -> str | None:
    # PCTCode: the Pakistan Customs Tariff code, up to 8 digits. Items don't carry one yet (the branch's default stands in).
    own = getattr(product, "pct_code", None)
    return (own or settings.default_pct_code or None)


def _payment_mode(codes: list[str]) -> int:
    modes = {PAYMENT_MODES.get(code, 1) for code in codes}
    return modes.pop() if len(modes) == 1 else MIXED if modes else 1


def _buyer(party) -> dict:
    """BuyerName, BuyerNTN, BuyerCNIC, BuyerPhoneNumber: optional. Sent for a customer on the books (a credit or
    business customer, whose name SRO 1006 asks for), never for a walk-in."""
    if party is None or party.is_walk_in:
        return {"BuyerNTN": None, "BuyerCNIC": None, "BuyerName": None, "BuyerPhoneNumber": None}
    cnic = re.sub(r"\D", "", party.cnic or "")
    return {
        "BuyerNTN": (party.ntn or "").strip()[:9] or None,
        "BuyerCNIC": cnic if len(cnic) == 13 else None,
        "BuyerName": party.name[:150],
        "BuyerPhoneNumber": (party.phone or "").strip()[:20] or None,
    }


def _totals(items: list[dict], extra: Decimal = ZERO) -> dict:
    return {
        # TotalBillAmount: what the invoice comes to (the lines with their tax, plus any fare on the bill).
        "TotalBillAmount": _num(sum((Decimal(str(i["TotalAmount"])) for i in items), ZERO) + extra),
        # TotalQuantity: the document says "Count of Total Items" beside a sample of 15; the quantities are added up.
        "TotalQuantity": float(sum((Decimal(str(i["Quantity"])) for i in items), ZERO)),
        # TotalSaleValue: the lines' sale values (after discount, before tax).
        "TotalSaleValue": _num(sum((Decimal(str(i["SaleValue"])) for i in items), ZERO)),
        # TotalTaxCharged: the lines' tax.
        "TotalTaxCharged": _num(sum((Decimal(str(i["TaxCharged"])) for i in items), ZERO)),
        # Discount: the lines' discounts.
        "Discount": _num(sum((Decimal(str(i["Discount"])) for i in items), ZERO)),
        # FurtherTax: charged on supplies to unregistered business buyers; a retail bill carries none.
        "FurtherTax": 0.0,
    }


async def _sale_payload(sale: SaleRecord, settings: FbrSettings) -> dict:
    """A bill as FBR's invoice. A return line rung on the bill (Billing's return mode) goes as its own credit line: item
    InvoiceType 3 with the bill it came from in RefUSIN, and its figures negative, so the totals are what was paid."""
    from app.models import Party

    party = await Party.get_or_none(id=sale.party_id)
    items = []
    for line in await SaleLine.filter(sale_id=sale.id).prefetch_related("product"):
        sign = Decimal("-1") if line.is_return else Decimal("1")
        gross = abs(Decimal(line.level_qty) * Decimal(line.level_price)) if line.sell_level and line.level_qty is not None and line.level_price is not None \
            else abs(Decimal(line.qty) * Decimal(line.unit_price))
        disc = abs(Decimal(line.disc_amount or 0))
        value = gross - disc
        tax = abs(Decimal(line.tax_amount)) if line.tax_amount is not None else value * Decimal(line.product.tax_rate) / Decimal("100")
        items.append({
            "ItemCode": line.product.sku,                              # the Item's code
            "ItemName": line.product.name[:150],
            "Quantity": float(sign * abs(Decimal(line.qty))),         # in the Item's stocked unit
            "PCTCode": _pct_code(line.product, settings),
            "TaxRate": float(line.product.tax_rate),                   # percent, 0 for an exempt Item
            "SaleValue": _num(sign * value),                           # after discount, before tax
            "TotalAmount": _num(sign * (value + tax)),                 # sale value plus tax
            "TaxCharged": _num(sign * tax),
            "Discount": _num(sign * disc),                             # the Item's own discount and its share of the bill's
            "FurtherTax": 0.0,
            "InvoiceType": CREDIT if line.is_return else NEW,          # 11 and 12 are for Third Schedule goods: not told apart yet
            "RefUSIN": line.return_of_invoice if line.is_return else None,
        })
    codes = [t.code for t in await SaleTender.filter(sale_id=sale.id) if Decimal(t.amount) > 0]
    return {
        "InvoiceNumber": "",                                           # blank: FBR fills it
        "POSID": None,                                                 # the counter's POS ID, put in when it is sent
        "USIN": sale.invoice_number,                                   # this POS's own number for the bill
        "DateTime": pk_time(sale.at).strftime("%Y-%m-%d %H:%M:%S"),    # Pakistan time, as the till's clock reads it
        **_buyer(party),
        **_totals(items, Decimal(sale.fare or 0)),
        "PaymentMode": _payment_mode(codes),
        "RefUSIN": None,
        "InvoiceType": NEW,
        "Items": items,
    }


async def _return_payload(record: ReturnRecord, sale: SaleRecord, settings: FbrSettings) -> dict:
    """A return as FBR's credit invoice (InvoiceType 3) naming the bill it is against in RefUSIN, on the invoice and on
    every line. Each line is what the customer paid for it, so the figures are positive and the type says it is a credit."""
    from app.models import Party

    party = await Party.get_or_none(id=sale.party_id)
    items = []
    for line in await ReturnLine.filter(return_record_id=record.id).prefetch_related("product"):
        total = (Decimal(line.qty) * Decimal(line.unit_price)).quantize(CENT, rounding=ROUND_HALF_UP)
        rate = Decimal(line.product.tax_rate or 0)
        tax = Decimal(line.tax_amount) if line.tax_amount is not None else (total - total / (1 + rate / Decimal("100")))
        items.append({
            "ItemCode": line.product.sku,
            "ItemName": line.product.name[:150],
            "Quantity": float(Decimal(line.qty)),
            "PCTCode": _pct_code(line.product, settings),
            "TaxRate": float(rate),
            "SaleValue": _num(total - tax),
            "TotalAmount": _num(total),
            "TaxCharged": _num(tax),
            # The price refunded is what was paid, after the bill's discounts, so there is no discount left to show.
            "Discount": 0.0,
            "FurtherTax": 0.0,
            "InvoiceType": CREDIT,
            "RefUSIN": sale.invoice_number,
        })
    return {
        "InvoiceNumber": "",
        "POSID": None,
        "USIN": record.number or f"RT-{str(record.id)[:8].upper()}",
        "DateTime": pk_time(record.at).strftime("%Y-%m-%d %H:%M:%S"),
        **_buyer(party),
        **_totals(items),
        "PaymentMode": _payment_mode([record.refund_method or "CASH"]),
        "RefUSIN": sale.invoice_number,
        "InvoiceType": CREDIT,
        "Items": items,
    }


# Issuing, inside the bill's or the return's own transaction:

async def issue_for_sale(sale: SaleRecord) -> None:
    """Called by the sales service once a bill's lines and payments are written, in the same transaction. Dummy: the
    test number goes on the bill now. Sandbox or production: the invoice is written, ready to send once the bill has
    committed (send_after_commit, and the loop)."""
    settings = await _settings()
    mode = settings.mode if settings.mode in MODES else DEFAULT_MODE
    if mode == "off":
        return
    counter = await _counter_of(sale.till_session_id)
    pos_id = (settings.pos_ids or {}).get(str(counter.id)) if counter else None
    common = {"kind": "sale", "sale": sale, "usin": sale.invoice_number, "counter_id": str(counter.id) if counter else None, "pos_id": pos_id}
    if mode == "dummy":
        number = await _dummy_number(counter)
        await FbrInvoice.create(**common, mode="dummy", status="dummy", fbr_invoice_number=number)
        sale.fbr_invoice_number = number
        await sale.save(update_fields=["fbr_invoice_number"])
        return
    await FbrInvoice.create(
        **common, mode=mode, status="waiting", payload=await _sale_payload(sale, settings), next_try_at=datetime.now(timezone.utc),
    )


async def issue_for_return(record: ReturnRecord, sale: SaleRecord) -> None:
    """The same for a return, once its lines are written: a credit note against `sale`."""
    settings = await _settings()
    mode = settings.mode if settings.mode in MODES else DEFAULT_MODE
    if mode == "off":
        return
    counter = await _counter_of(record.till_session_id)
    pos_id = (settings.pos_ids or {}).get(str(counter.id)) if counter else None
    common = {
        "kind": "return", "return_record": record, "usin": record.number or f"RT-{str(record.id)[:8].upper()}",
        "ref_usin": sale.invoice_number, "counter_id": str(counter.id) if counter else None, "pos_id": pos_id,
    }
    if mode == "dummy":
        await FbrInvoice.create(**common, mode="dummy", status="dummy", fbr_invoice_number=await _dummy_number(counter))
        return
    await FbrInvoice.create(
        **common, mode=mode, status="waiting", payload=await _return_payload(record, sale, settings), next_try_at=datetime.now(timezone.utc),
    )


# Sending:

_in_flight: set[str] = set()
# When FBR last couldn't be reached (time.monotonic()), or None once it has answered since.
_last_failure: float | None = None


def _went_down() -> None:
    global _last_failure
    _last_failure = time.monotonic()


def _looks_down() -> bool:
    seconds = float(os.environ.get("FBR_DOWN_SECONDS", DOWN_FOR_SECONDS))
    return _last_failure is not None and time.monotonic() - _last_failure < seconds


def _retry_after(attempts: int) -> timedelta:
    return timedelta(seconds=RETRY_STEPS[attempts - 1] if 0 < attempts <= len(RETRY_STEPS) else RETRY_MAX)


def _answer(response: httpx.Response):
    """FBR's answer as data. The document shows it both as JSON and as a JSON string inside JSON."""
    try:
        body = response.json()
    except ValueError:
        return {"text": response.text[:500]}
    if isinstance(body, str):
        with contextlib.suppress(ValueError):
            body = json.loads(body)
    return body if isinstance(body, dict) else {"body": body}


def _fbr_number(body: dict) -> str | None:
    """FBR's invoice number: "InvoiceNumber" in the fiscal component's answer, "FBRInvoiceNumber" in the web API's (the
    document shows both). It can be a bare number; it is kept as text, whole."""
    for key in ("InvoiceNumber", "FBRInvoiceNumber", "invoiceNumber", "fbrInvoiceNumber"):
        value = body.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def _refusal(body: dict) -> str:
    parts = [str(body.get(k)) for k in ("Response", "Errors", "ErrorMessage", "text") if body.get(k)]
    return ("; ".join(parts) or "FBR didn't send an invoice number back.")[:300]


async def _post(invoice: FbrInvoice, settings: FbrSettings, timeout: float) -> bool:
    """Sends one waiting invoice. True to go on with the next; False when the next would fare the same (FBR can't be
    reached, or it refused the token), so a batch stops there instead of waiting on every invoice in turn."""
    global _last_failure
    now = datetime.now(timezone.utc)
    pos_id = invoice.pos_id or (settings.pos_ids or {}).get(invoice.counter_id or "")
    problem = None
    if not settings.access_token:
        problem = "No FBR access token is saved. Enter it in FBR invoices settings."
    elif not pos_id:
        problem = "This counter has no FBR POS ID. Enter it in FBR invoices settings."
    if problem:
        invoice.attempts += 1
        invoice.last_error, invoice.next_try_at = problem, now + _retry_after(invoice.attempts)
        await invoice.save(update_fields=["attempts", "last_error", "next_try_at"])
        return True

    payload = dict(invoice.payload or {})
    payload["POSID"] = int(pos_id)  # POSID: the POS registration number FBR gave this counter (a whole number)
    invoice.attempts += 1
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=os.environ.get("FBR_VERIFY_TLS", "on").lower() != "off") as client:
            # The token goes as "Authorization: Bearer <token>", as the PRAL document shows. It is never logged.
            response = await client.post(_endpoint(invoice.mode), json=payload, headers={"Authorization": f"Bearer {settings.access_token}"})
    except httpx.HTTPError as exc:
        _went_down()
        invoice.last_error = f"FBR couldn't be reached ({type(exc).__name__})."
        invoice.next_try_at = now + _retry_after(invoice.attempts)
        await invoice.save(update_fields=["attempts", "last_error", "next_try_at"])
        logs.log.warning("fbr: %s %s waits, FBR couldn't be reached (%s)", invoice.kind, invoice.usin, type(exc).__name__)
        return False

    body = _answer(response)
    number = _fbr_number(body)
    code = str(body.get("Code") or body.get("code") or "")
    if response.status_code >= 500 or response.status_code in (401, 403, 408, 429):
        # FBR's side, or the token: nothing about the invoice to fix, so it waits and goes again.
        if response.status_code not in (401, 403):
            _went_down()
        invoice.response, invoice.next_try_at = body, now + _retry_after(invoice.attempts)
        invoice.last_error = ("FBR refused the access token. Check it in FBR invoices settings." if response.status_code in (401, 403)
                              else f"FBR answered {response.status_code}. It will be sent again.")
        await invoice.save(update_fields=["attempts", "response", "last_error", "next_try_at"])
        logs.log.warning("fbr: %s %s waits, FBR answered %s", invoice.kind, invoice.usin, response.status_code)
        return False
    _last_failure = None
    if response.status_code < 300 and number and code in ("100", ""):
        invoice.status, invoice.fbr_invoice_number, invoice.response = "posted", number, body
        invoice.payload, invoice.posted_at, invoice.last_error, invoice.next_try_at = payload, now, None, None
        await invoice.save(update_fields=["status", "fbr_invoice_number", "response", "payload", "posted_at", "last_error", "next_try_at", "attempts"])
        if invoice.sale_id and len(number) <= 30:
            await SaleRecord.filter(id=invoice.sale_id).update(fbr_invoice_number=number)
        logs.log.info("fbr: %s %s posted to %s, FBR invoice %s", invoice.kind, invoice.usin, invoice.mode, number)
        return True
    # FBR read it and said no: sending the same again won't change that. It waits for a person.
    invoice.status, invoice.response, invoice.last_error, invoice.next_try_at = "refused", body, _refusal(body), None
    invoice.payload = payload
    await invoice.save(update_fields=["status", "response", "last_error", "next_try_at", "attempts", "payload"])
    logs.log.warning("fbr: %s %s refused by FBR (code %s)", invoice.kind, invoice.usin, code or response.status_code)
    return True


async def _send(invoices: list[FbrInvoice], timeout: float) -> int:
    """Sends these in turn, stopping at the first that can't reach FBR. The number posted."""
    settings = await _settings()
    if settings.mode not in LIVE_MODES:
        return 0
    posted = 0
    for invoice in invoices:
        key = str(invoice.id)
        if key in _in_flight or invoice.mode != settings.mode:
            continue
        _in_flight.add(key)
        try:
            await invoice.refresh_from_db()
            if invoice.status != "waiting":
                continue
            go_on = await _post(invoice, settings, timeout)
            if invoice.status == "posted":
                posted += 1
            if not go_on:
                break
        finally:
            _in_flight.discard(key)
    return posted


async def send_after_commit(*, sales: list[SaleRecord] = (), returns: list[ReturnRecord] = ()) -> None:
    """Right after a bill or a return has committed: one short try, so the receipt can carry FBR's number. When FBR has
    just failed, it doesn't try at all and the invoice waits for the loop. Never raises: the sale is done either way."""
    try:
        if await current_mode() not in LIVE_MODES or _looks_down():
            return
        sale_ids = [s.id for s in sales if s is not None]
        return_ids = [r.id for r in returns if r is not None]
        if not sale_ids and not return_ids:
            return
        waiting = []
        if sale_ids:
            waiting += await FbrInvoice.filter(status="waiting", sale_id__in=sale_ids).order_by("created_at")
        if return_ids:
            waiting += await FbrInvoice.filter(status="waiting", return_record_id__in=return_ids).order_by("created_at")
        await _send(waiting, INLINE_SECONDS)
    except Exception as exc:  # noqa: BLE001 — the bill is already made; FBR only ever waits
        logs.log.error("fbr: sending right after the sale failed", exc_info=exc)


async def send_due(limit: int = 25, timeout: float = 10.0) -> int:
    """The loop's round: the invoices due to go, oldest first. While FBR looks down only the oldest one goes, as a probe;
    once one gets through the rest follow."""
    mode = await current_mode()
    if mode not in LIVE_MODES:
        return 0
    due = FbrInvoice.filter(status="waiting", mode=mode, next_try_at__lte=datetime.now(timezone.utc)).order_by("created_at")
    return await _send(list(await due.limit(1 if _looks_down() else limit)), timeout)


async def send_again(user: User) -> dict:
    """The Branch Manager's "Send now": every invoice waiting or refused goes again at once, in the mode the branch is
    in now (an invoice made in the sandbox goes to production if the branch has switched)."""
    from app.core.abilities import BRANCH_MANAGER

    if user.role_id != BRANCH_MANAGER:
        raise FbrError("Only a Branch Manager can send FBR invoices again.", 403)
    mode = await current_mode()
    if mode not in LIVE_MODES:
        raise FbrError("Invoices are sent to FBR in Sandbox or Production. Switch the mode first.")
    await FbrInvoice.filter(status__in=("waiting", "refused")).update(status="waiting", mode=mode, next_try_at=datetime.now(timezone.utc))
    global _last_failure
    _last_failure = None
    await send_due(limit=50, timeout=8.0)
    return await queue_out()


# The waiting list:

async def queue_summary() -> dict:
    waiting = await FbrInvoice.filter(status="waiting").count()
    refused = await FbrInvoice.filter(status="refused").count()
    oldest = await FbrInvoice.filter(status__in=("waiting", "refused")).order_by("created_at").first()
    return {"waiting": waiting, "refused": refused, "oldestAt": oldest.created_at if oldest else None}


async def queue_out(limit: int = 200) -> dict:
    rows = await FbrInvoice.filter(status__in=("waiting", "refused")).order_by("-created_at").limit(limit)
    counters = {str(c.id): c.code for c in await SalesCounter.all()}
    return {
        **await queue_summary(),
        "mode": await current_mode(),
        "items": [{
            "id": str(r.id), "kind": r.kind, "usin": r.usin, "refUsin": r.ref_usin, "counter": counters.get(r.counter_id or ""),
            "mode": r.mode, "status": r.status, "attempts": r.attempts, "lastError": r.last_error, "nextTryAt": r.next_try_at,
            "createdAt": r.created_at,
        } for r in rows],
    }


# What receipts print:

def _stamp(invoice: FbrInvoice | None, settings: FbrSettings, legacy_number: str | None = None) -> dict:
    if invoice is None:
        # Bills from before FBR invoices were kept here carry the number made from the bill number: a test number too.
        return {"status": "dummy" if legacy_number else "off", "number": legacy_number or None, "posId": None, "usin": None,
                "sandbox": False, "taxOffice": settings.tax_office}
    pos_id = invoice.pos_id or (settings.pos_ids or {}).get(invoice.counter_id or "")
    return {
        "status": invoice.status, "number": invoice.fbr_invoice_number, "posId": pos_id, "usin": invoice.usin,
        "sandbox": invoice.mode == "sandbox", "taxOffice": settings.tax_office,
    }


async def stamps_for_sales(sales: list[SaleRecord]) -> dict[str, dict]:
    """The FBR block of each bill's receipt, by bill id, in one read."""
    if not sales:
        return {}
    settings = await _settings()
    rows = {str(r.sale_id): r for r in await FbrInvoice.filter(sale_id__in=[s.id for s in sales], kind="sale")}
    return {str(s.id): _stamp(rows.get(str(s.id)), settings, s.fbr_invoice_number) for s in sales}


async def stamp_for_sale(sale: SaleRecord) -> dict:
    return (await stamps_for_sales([sale]))[str(sale.id)]


async def stamp_for_return(record: ReturnRecord) -> dict | None:
    """A return's FBR block. Returns from before FBR invoices were kept here have none."""
    invoice = await FbrInvoice.get_or_none(return_record_id=record.id, kind="return")
    return _stamp(invoice, await _settings()) if invoice else None


# The loop:

_task: asyncio.Task | None = None


async def _loop() -> None:
    await asyncio.sleep(float(os.environ.get("FBR_LOOP_DELAY_SECONDS", "15")))
    while True:
        try:
            await send_due()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the loop must outlive anything one round can do
            logs.log.error("fbr: sending waiting invoices failed", exc_info=exc)
        await asyncio.sleep(max(float(os.environ.get("FBR_LOOP_SECONDS", LOOP_SECONDS)), 2.0))


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="fbr-invoices")


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
    _task = None
