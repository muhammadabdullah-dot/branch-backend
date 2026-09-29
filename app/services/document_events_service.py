"""What a branch tells head office about a document it has just made.

Head office has kept a branch's *figures* for a long time: a day's takings, an hour's invoices, a product's quantity.
It now keeps the documents as well, and this is what fills them.

The events themselves are not new. Every bill a branch rings has produced a `SaleRecord` event since the beginning,
and 634 of them are sitting at head office right now marked `stored`, because until this week nothing there could
apply one. What was new is that the payload carried four fields — the invoice number, the net value, a party id and a
member code — which is enough to say a sale happened and nothing like enough to be the bill.

So the payload is built here, in one place, rather than at each of the four points where a document is made. Three
reasons, and the third is the one that bites:

  * a return is emitted from two different places today, with two different payloads;
  * FBR invoices were never sent to head office at all;
  * and head office's `branch_documents_service` reads one shape. Two writers inventing their own would drift, and a
    drift in a payload is silent: the document lands, it is simply missing something nobody notices until they go
    looking for it a year later.

**Names, not ids.** Head office does not hold a branch's users, parties or counters, so an id would be a dead end
there. The name is what somebody reading a bill needs; the branch's own ids stay on the branch.

**The trading day travels with the document.** A shop selling past midnight files a bill under the day that is still
going on, and every figure head office holds is grouped that way. If the day were worked out at head office from the
timestamp, the document and the figures beside it would disagree about which day it belonged to.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.device_context import get_device_id
from app.core.pk_time import pk_day
from app.models import FbrInvoice, OutboxEvent, ReturnRecord, SaleRecord, User

ZERO = Decimal("0")


def _s(value) -> str:
    return format(Decimal(str(value if value not in (None, "") else 0)), "f")


def _first(*values):
    """The first one that was actually given. A zero is a figure, not a missing one."""
    for value in values:
        if value is not None:
            return value
    return None


# ── a bill ───────────────────────────────────────────────────────────────────────────────────────
async def sale_payload(sale: SaleRecord) -> dict:
    """The whole bill: its own figures, every line, and how it was paid.

    `cogs` is added up here from the lines' own costs rather than left for head office, because the cost of a line is
    what the Item cost *on the day it was sold* and the branch is the only side that still knows it: head office's
    copy of the Item carries today's average cost, not that day's.
    """
    await sale.fetch_related("lines__product", "lines__promotion", "tenders", "party", "cashier",
                             "till_session__counter", "member")
    lines = sorted(sale.lines, key=lambda l: str(l.id))
    cogs = sum((Decimal(str(l.qty or 0)) * Decimal(str(l.unit_cost or 0)) for l in lines if not l.is_return), ZERO)
    return {
        "invoiceNumber": sale.invoice_number,
        "at": sale.at.isoformat(),
        "day": pk_day(sale.at).isoformat(),
        "cashierName": sale.cashier.name if sale.cashier else None,
        "partyName": sale.party.name if sale.party else None,
        "partyCode": sale.party.code if sale.party else None,
        "tillSessionNumber": sale.till_session.session_number if sale.till_session else None,
        "counterName": (sale.till_session.counter.name
                        if sale.till_session and sale.till_session.counter else None),
        "gross": _s(sale.gross), "discTotal": _s(sale.disc_total), "fare": _s(sale.fare), "gst": _s(sale.gst),
        "grandTotal": _s(sale.grand_total), "netValue": _s(sale.net_value),
        "received": _s(sale.received), "cashBack": _s(sale.cash_back), "cogs": _s(cogs),
        "isCreditSale": bool(sale.is_credit_sale),
        "fbrInvoiceNumber": sale.fbr_invoice_number,
        "memberCode": sale.member.code if sale.member else None,
        "earnedPoints": int(sale.earned_points or 0),
        "lines": [
            {
                "lineNo": i + 1,
                "productSku": l.product.sku if l.product else None,
                "productName": l.product.name if l.product else None,
                "department": l.product.department if l.product else None,
                "qty": _s(l.qty), "unitPrice": _s(l.unit_price),
                "discAmount": _s(l.disc_amount), "taxAmount": _s(l.tax_amount), "unitCost": _s(l.unit_cost),
                "isReturn": bool(l.is_return), "aliasCode": l.alias_code,
                # By code, not by id: head office writes the campaigns and knows them by code, and a branch that has
                # not yet been sent one would otherwise send an id head office cannot resolve.
                "promotionCode": l.promotion.code if getattr(l, "promotion", None) else None,
                "sellLevel": l.sell_level,
            }
            for i, l in enumerate(lines)
        ],
        "tenders": [
            {"code": t.code, "amount": _s(t.amount),
             # Whatever proves the money arrived, in one field: a card's number, a transfer's reference, the
             # approval code. Head office has one column for it because it only ever reads it, never matches on it.
             "reference": t.reference or t.transaction_id or t.proof}
            for t in sale.tenders
        ],
    }


async def emit_sale(sale: SaleRecord, cashier: User) -> None:
    await OutboxEvent.create(
        aggregate_type="SaleRecord", aggregate_id=str(sale.id), payload=await sale_payload(sale),
        origin_user_id=str(cashier.id), origin_device_id=get_device_id(),
    )


# ── a return ─────────────────────────────────────────────────────────────────────────────────────
async def return_payload(record: ReturnRecord, sale: SaleRecord | None = None, **extra) -> dict:
    """What came back, and off which bill. `extra` carries the few things only one of the two callers knows: the
    exchange it became, or the voucher it was refunded onto."""
    await record.fetch_related("lines__product", "cashier", "till_session", "against")
    against = sale or record.against
    return {
        "number": record.number,
        "at": record.at.isoformat(),
        "day": pk_day(record.at).isoformat(),
        "againstInvoice": against.invoice_number if against else None,
        "cashierName": record.cashier.name if record.cashier else None,
        "refundTotal": _s(record.refund_total), "taxTotal": _s(record.tax_total),
        "refundMethod": record.refund_method, "reason": record.reason, "note": record.note,
        "tillSessionNumber": record.till_session.session_number if record.till_session else None,
        "kind": record.kind,
        "lines": [
            {
                "lineNo": i + 1,
                "productSku": l.product.sku if l.product else None,
                "productName": l.product.name if l.product else None,
                "qty": _s(l.qty), "unitPrice": _s(l.unit_price),
                "taxAmount": _s(l.tax_amount), "unitCost": _s(l.unit_cost),
            }
            for i, l in enumerate(sorted(record.lines, key=lambda l: str(l.id)))
        ],
        **{k: v for k, v in extra.items() if v is not None},
    }


async def emit_return(record: ReturnRecord, user: User, sale: SaleRecord | None = None, **extra) -> None:
    await OutboxEvent.create(
        aggregate_type="ReturnRecord", aggregate_id=str(record.id),
        payload=await return_payload(record, sale, **extra),
        origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )


# ── what FBR was told ────────────────────────────────────────────────────────────────────────────
async def fbr_payload(invoice: FbrInvoice) -> dict:
    """The stamp, and the lines as they were declared.

    The declared lines come out of the invoice's own stored `payload`, which is the documented JSON that went to FBR,
    rather than being read back off the bill. What was sold and what was declared are two facts, and when FBR queries
    a return it is the declared figures that have to be produced.
    """
    await invoice.fetch_related("sale", "return_record")
    document = invoice.sale or invoice.return_record
    at = getattr(document, "at", None) or invoice.created_at
    sent = invoice.payload if isinstance(invoice.payload, dict) else {}
    declared = sent.get("items") or sent.get("Items") or []
    return {
        "invoiceNumber": invoice.usin,
        "kind": invoice.kind,
        "at": at.isoformat(),
        "day": pk_day(at).isoformat(),
        "fbrInvoiceNumber": invoice.fbr_invoice_number,
        # Their word for it, as their own column holds it: dummy, waiting, posted, refused.
        "status": invoice.status,
        "posId": invoice.pos_id,
        # What was declared, where FBR's own JSON says it; otherwise what the document itself came to. `is not
        # None` rather than `or`, because a genuine zero is an answer and `or` would throw it away.
        "total": _s(_first(sent.get("TotalBillAmount"), getattr(document, "net_value", None),
                           getattr(document, "refund_total", None))),
        "taxTotal": _s(_first(sent.get("TotalTaxCharged"), getattr(document, "gst", None),
                              getattr(document, "tax_total", None))),
        "message": invoice.last_error,
        "buyerName": sent.get("BuyerName"), "buyerNtn": sent.get("BuyerNTN"), "buyerCnic": sent.get("BuyerCNIC"),
        "lines": [
            {
                "lineNo": i + 1,
                "productSku": item.get("ItemCode") or item.get("PCTCode"),
                "productName": item.get("ItemName"),
                "hsCode": item.get("PCTCode") or item.get("HSCode"),
                "qty": _s(item.get("Quantity")), "unitPrice": _s(item.get("SaleValue") or item.get("Rate")),
                "discAmount": _s(item.get("Discount")), "taxRate": _s(item.get("TaxRate")),
                "taxAmount": _s(item.get("TaxCharged")), "total": _s(item.get("TotalAmount")),
            }
            for i, item in enumerate(declared if isinstance(declared, list) else [])
        ],
    }


async def emit_fbr(invoice: FbrInvoice, user: User | None = None) -> None:
    """Sent every time the stamp changes state, because waiting, posted and refused are all worth knowing at head
    office and the last one is worth knowing quickly. Head office keys on the branch, the bill and the kind, so the
    later message simply updates the earlier one."""
    await OutboxEvent.create(
        aggregate_type="FbrInvoice", aggregate_id=str(invoice.id), payload=await fbr_payload(invoice),
        origin_user_id=str(user.id) if user else None, origin_device_id=get_device_id(),
    )
