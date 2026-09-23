"""Asking head office for stock. See models/requisition.py for the request's life.

  1. Someone here writes a draft: Items, quantities, a reason, when it's needed, and who should send it (head
     office's godown, or another branch through head office). Each line shows what this branch holds and how many
     days that lasts at the last 30 days' sales, so the quantity isn't a guess.
  2. Sending it queues an event for head office (the same outbox every transfer answer uses).
  3. Head office approves it, changing quantities if it must, or declines it with a reason. The answer comes back
     down; an approval arrives together with the transfer it became, which this branch receives as usual.

A request that has been sent can be withdrawn until head office decides. A draft can be thrown away.

A request can ask for any Item the company has, not only the ones this branch carries: the new-request window searches
head office's list live (`company_items`). A line for an Item this branch doesn't carry yet goes by its code, name and
unit, with no Item of its own here; the Item joins this branch's list when stock of it comes.
"""
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
from tortoise.functions import Sum
from tortoise.transactions import atomic

from app.core.pk_time import today_pk
from app.core.device_context import get_device_id
from app.models import KnownBranch, OutboxEvent, Product, ReturnLine, SaleLine, StockMovement, User
from app.models.requisition import StockRequest, StockRequestLine
from app.services import numbering_service

ZERO = Decimal("0")
COVER_WINDOW_DAYS = 30
# Items selling here that would run out within a week are suggested, topped up to two weeks.
LOW_COVER_DAYS = 7
TARGET_COVER_DAYS = 14
OPEN = ("draft", "sent")
REQUESTERS = [("inventory.requests", "W")]
LINK = "/inventory/requests"


class RequestError(Exception):
    def __init__(self, message: str):
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── stock cover ────────────────────────────────────────────────────────────────────────────────

async def _on_hand(product_ids: list[str] | None) -> dict[str, Decimal]:
    qs = StockMovement.all()
    if product_ids is not None:
        qs = qs.filter(product_id__in=product_ids)
    rows = await qs.annotate(total=Sum("qty")).group_by("product_id").values("product_id", "total")
    return {str(r["product_id"]): Decimal(str(r["total"] or 0)) for r in rows}


async def _sold(product_ids: list[str] | None, since: datetime) -> dict[str, Decimal]:
    """Units sold since a time, less what came back: return lines on a bill and returns taken at the counter."""
    out: dict[str, Decimal] = {}
    qs = SaleLine.filter(sale__at__gte=since)
    if product_ids is not None:
        qs = qs.filter(product_id__in=product_ids)
    for is_return in (False, True):
        rows = await qs.filter(is_return=is_return).annotate(total=Sum("qty")).group_by("product_id").values("product_id", "total")
        for r in rows:
            sign = -1 if is_return else 1
            out[str(r["product_id"])] = out.get(str(r["product_id"]), ZERO) + sign * Decimal(str(r["total"] or 0))
    rqs = ReturnLine.filter(return_record__at__gte=since)
    if product_ids is not None:
        rqs = rqs.filter(product_id__in=product_ids)
    for r in await rqs.annotate(total=Sum("qty")).group_by("product_id").values("product_id", "total"):
        out[str(r["product_id"])] = out.get(str(r["product_id"]), ZERO) - Decimal(str(r["total"] or 0))
    return {k: max(v, ZERO) for k, v in out.items()}


def _cover(on_hand: Decimal, daily: Decimal) -> Decimal | None:
    if daily <= ZERO:
        return None
    return (max(on_hand, ZERO) / daily).quantize(Decimal("0.1"))


async def cover_for(product_ids: list[str]) -> dict[str, dict]:
    """What this branch holds of each Item, what it sells a day (last 30 days) and how many days that lasts."""
    ids = list(dict.fromkeys(str(p) for p in product_ids if p))[:500]
    if not ids:
        return {}
    since = _now() - timedelta(days=COVER_WINDOW_DAYS)
    held, sold = await _on_hand(ids), await _sold(ids, since)
    out = {}
    for pid in ids:
        on_hand = held.get(pid, ZERO)
        daily = (sold.get(pid, ZERO) / COVER_WINDOW_DAYS).quantize(Decimal("0.001"))
        out[pid] = {"onHand": on_hand, "dailySales": daily, "daysCover": _cover(on_hand, daily)}
    return out


async def suggestions(limit: int = 50) -> list[dict]:
    """Items selling here that run out within a week at the last 30 days' pace, with the quantity that brings
    them to two weeks. Items already on a draft or sent request are left out."""
    since = _now() - timedelta(days=COVER_WINDOW_DAYS)
    sold = await _sold(None, since)
    if not sold:
        return []
    held = await _on_hand(list(sold))
    on_requests = set(
        await StockRequestLine.filter(request__status__in=list(OPEN)).values_list("product_id", flat=True)
    )
    rows = []
    for pid, units in sold.items():
        if units <= ZERO or pid in on_requests:
            continue
        daily = units / COVER_WINDOW_DAYS
        on_hand = max(held.get(pid, ZERO), ZERO)
        days = on_hand / daily
        if days >= LOW_COVER_DAYS:
            continue
        want = Decimal(math.ceil(daily * TARGET_COVER_DAYS - on_hand))
        if want <= ZERO:
            continue
        rows.append((days, pid, on_hand, daily, want))
    rows.sort(key=lambda r: (r[0], r[1]))
    rows = rows[: max(1, min(limit, 200))]
    products = {p.id: p for p in await Product.filter(id__in=[r[1] for r in rows])}
    out = []
    for days, pid, on_hand, daily, want in rows:
        p = products.get(pid)
        if not p or not getattr(p, "active", True):
            continue
        out.append({
            "productId": pid, "productName": p.name, "productSku": p.sku, "unit": p.unit, "onHand": on_hand,
            "dailySales": daily.quantize(Decimal("0.001")), "daysCover": days.quantize(Decimal("0.1")), "suggestedQty": want,
        })
    return out


# ── reading ────────────────────────────────────────────────────────────────────────────────────

async def list_requests(status: str | None = None) -> list[StockRequest]:
    qs = StockRequest.all()
    if status:
        qs = qs.filter(status=status)
    return await qs.prefetch_related("lines__product").order_by("-created_at")


async def get(request_id: str) -> StockRequest:
    found = await StockRequest.get_or_none(id=request_id).prefetch_related("lines__product")
    if not found:
        raise RequestError("That stock request doesn't exist.")
    return found


async def company_items(q: str, limit: int = 30) -> list[dict]:
    """Every Item the company has that matches `q` (name, code or barcode), from head office's list, asked live: the
    godown's Items with what the godown holds, and Items only other branches carry with what each holds. Each says
    whether this branch carries it too, and how much it holds."""
    identity = await _identity()
    if identity is None:
        raise RequestError("This branch isn't paired with head office yet, so only Items it already has can be asked for.")
    if not (q or "").strip():
        return []
    from app.services.sync_service import _headers

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{identity.cloud_url}/sync/company-items", params={"q": q.strip(), "limit": min(max(limit, 1), 60)},
                headers=_headers(identity),
            )
    except httpx.HTTPError:
        raise RequestError("Head office can't be reached right now, so its Item list can't be searched. Items this branch has can still be added.")
    if response.status_code >= 400:
        raise RequestError(f"Head office couldn't search its Item list ({response.status_code}). Items this branch has can still be added.")
    found = [row for row in response.json() if row.get("sku")]
    here = {p.sku: p for p in await Product.filter(sku__in=[row["sku"] for row in found])}
    held = await _on_hand([str(p.id) for p in here.values()]) if here else {}
    out = []
    for row in found:
        mine = here.get(row["sku"])
        out.append({
            **row, "productId": str(mine.id) if mine else None,
            "onHandHere": held.get(str(mine.id), ZERO) if mine else None,
            "branches": [b for b in row.get("branches") or [] if b.get("branchCode") != identity.code],
        })
    return out


async def sources() -> list[KnownBranch]:
    return await KnownBranch.all().order_by("name")


# ── writing ────────────────────────────────────────────────────────────────────────────────────

async def _identity():
    from app.services import registration_service

    return await registration_service.current()


async def _check_source(source_code: str | None) -> tuple[str | None, str | None]:
    code = (source_code or "").strip().upper() or None
    if code is None:
        return None, None
    identity = await _identity()
    if identity and identity.code.upper() == code:
        raise RequestError("A branch can't ask itself for stock. Ask head office or another branch.")
    branch = await KnownBranch.get_or_none(code=code)
    if not branch:
        raise RequestError("Pick a branch from the list head office sent. If it's missing, check head office now.")
    return branch.code, branch.name


async def _check_lines(lines: list[dict]) -> list[dict]:
    """Each line as it will be saved: the Item this branch carries, or for one it doesn't yet, the code, name and unit
    head office's list gave. An Item this branch has becomes its own line even when it was picked by code."""
    out: list[dict] = []
    seen: set[str] = set()
    for line in lines:
        qty = line.get("qty")
        product = None
        if line.get("productId"):
            product = await Product.get_or_none(id=line["productId"])
            if not product:
                raise RequestError(f"Unknown Item {line['productId']}")
        sku = str(line.get("sku") or "").strip()
        if product is None:
            if not sku:
                raise RequestError("A line has no Item. Pick it again from the list.")
            product = await Product.get_or_none(sku=sku)
        name = product.name if product else str(line.get("name") or "").strip()
        if product is None and not name:
            raise RequestError(f"Item {sku} has no name. Pick it again from head office's list.")
        code = product.sku if product else sku
        if code in seen:
            raise RequestError("An Item is on the request twice. Put the whole quantity on one line.")
        seen.add(code)
        if qty is None or qty <= ZERO:
            raise RequestError(f"{name}: enter a quantity above zero.")
        unit = product.unit if product else (str(line.get("unit") or "").strip()[:30] or None)
        out.append({"product": product, "sku": code, "name": name[:200], "unit": unit, "qty": qty})
    return out


@atomic()
async def save_draft(
    user: User, request_id: str | None, source_code: str | None, reason: str | None, needed_by: date | None,
    lines: list[dict],
) -> StockRequest:
    """Write or change a draft. Nothing reaches head office until it is sent. Each line is {productId} for an Item
    this branch carries, or {sku, name, unit} for one from head office's list, with its qty."""
    code, name = await _check_source(source_code)
    checked = await _check_lines(lines)
    if request_id:
        request = await StockRequest.get_or_none(id=request_id)
        if not request:
            raise RequestError("That stock request doesn't exist.")
        if request.status != "draft":
            raise RequestError(f"{request.number} has been sent, so it can't be changed. Withdraw it and write a new one.")
    else:
        identity = await _identity()
        prefix = f"{identity.code}-RQ-" if identity else "RQ-"
        number = await numbering_service.next_number("stock_request", StockRequest, "number", prefix, 4)
        request = StockRequest(number=number, status="draft", created_by=user, created_by_name=user.name)
    request.source_code, request.source_name = code, name
    request.reason = (reason or "").strip()[:255] or None
    request.needed_by = needed_by
    await request.save()
    await StockRequestLine.filter(request=request).delete()
    for line in checked:
        await StockRequestLine.create(
            request=request, product=line["product"], sku=line["sku"], name=line["name"], unit=line["unit"], qty_requested=line["qty"],
        )
    return await get(str(request.id))


@atomic()
async def delete_draft(user: User, request_id: str) -> None:
    request = await StockRequest.get_or_none(id=request_id)
    if not request:
        raise RequestError("That stock request doesn't exist.")
    if request.status != "draft":
        raise RequestError(f"{request.number} has been sent. Withdraw it instead, so head office is told.")
    await StockRequestLine.filter(request=request).delete()
    await request.delete()


@atomic()
async def send(user: User, request_id: str) -> StockRequest:
    """Send a draft to head office. What this branch holds and sells of each Item goes with it."""
    identity = await _identity()
    if identity is None:
        raise RequestError("This branch isn't verified with head office yet, so a request can't reach anyone.")
    request = await get(request_id)
    if request.status != "draft":
        raise RequestError(f"{request.number} was already sent.")
    if not request.lines:
        raise RequestError("Add at least one Item to the request.")
    if not (request.reason or "").strip():
        raise RequestError("Say why the stock is needed, so head office can decide.")
    if request.needed_by and request.needed_by < today_pk():
        raise RequestError("The needed-by date has passed. Pick today or a later day.")
    await _check_source(request.source_code)
    cover = await cover_for([str(line.product_id) for line in request.lines if line.product_id])
    now = _now()
    wire = []
    for line in request.lines:
        p = line.product
        # An Item this branch doesn't carry yet: it holds none and has sold none.
        c = cover.get(str(line.product_id), {}) if p else {"onHand": ZERO, "dailySales": ZERO}
        line.on_hand, line.daily_sales = c.get("onHand"), c.get("dailySales")
        await line.save(update_fields=["on_hand", "daily_sales"])
        if p:
            described = {"name": p.name, "unit": p.unit, "price": str(p.price), "taxRate": str(p.tax_rate), "isWeighed": p.is_weighed}
        else:
            # Head office knows this Item by its code; the name and unit only matter if it has to add it.
            described = {"name": line.name, "unit": line.unit}
        wire.append({
            "sku": line.sku or p.sku, **described, "qty": str(line.qty_requested),
            "onHand": None if line.on_hand is None else str(line.on_hand),
            "dailySales": None if line.daily_sales is None else str(line.daily_sales),
        })
    request.status, request.sent_at, request.sent_by_name = "sent", now, user.name
    await request.save()
    # Travels with the transfer events: head office hands every "Transfer" event to its transfer sync, which knows
    # requests too. The request's id is the same at head office, so its answer finds this record.
    await OutboxEvent.create(
        aggregate_type="Transfer", aggregate_id=str(request.id), origin_user_id=str(user.id), origin_device_id=get_device_id(),
        payload={"event": "requisition.sent", "requisition": {
            "id": str(request.id), "number": request.number, "sourceCode": request.source_code, "reason": request.reason,
            "neededBy": request.needed_by.isoformat() if request.needed_by else None, "requestedBy": request.created_by_name or user.name,
            "sentBy": user.name, "sentAt": now.isoformat(), "lines": wire,
        }},
    )
    return await get(request_id)


@atomic()
async def withdraw(user: User, request_id: str, reason: str | None) -> StockRequest:
    request = await get(request_id)
    if request.status == "draft":
        raise RequestError("A draft hasn't gone anywhere. Throw it away instead.")
    if request.status != "sent":
        raise RequestError(f"Head office has already decided on {request.number}.")
    now = _now()
    request.status, request.cancelled_at = "cancelled", now
    written = (reason or "").strip()
    request.decision_note = (f"Withdrawn by {user.name}: {written}" if written else f"Withdrawn by {user.name}")[:255]
    await request.save()
    await OutboxEvent.create(
        aggregate_type="Transfer", aggregate_id=str(request.id), origin_user_id=str(user.id), origin_device_id=get_device_id(),
        payload={"event": "requisition.cancelled", "requisitionId": str(request.id), "reason": written or None, "by": user.name, "at": now.isoformat()},
    )
    return await get(request_id)


# ── what head office sends back ──────────────────────────────────────────────────────────────────

def _dt(value):
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


_STATUS_FROM_HEAD_OFFICE = {"pending": "sent", "approved": "approved", "rejected": "declined", "cancelled": "cancelled"}


@atomic()
async def apply_update(state: dict) -> str:
    """Head office's copy of a request: received, approved (with the transfer it became) or declined. Applying it
    twice changes nothing. A request head office recorded for this branch arrives the same way and is added."""
    from app.services import alerts_service, transfers_service

    request_id = state.get("id")
    if not request_id:
        raise RequestError("A stock request from head office has no id.")
    incoming = _STATUS_FROM_HEAD_OFFICE.get(str(state.get("status") or ""), "sent")
    request = await StockRequest.get_or_none(id=request_id).prefetch_related("lines")
    before = request.status if request else None
    if request is None:
        request = await StockRequest.create(
            id=request_id, number=(state.get("branchNumber") or state.get("headOfficeNumber") or str(request_id)[:8])[:30],
            status=incoming, origin=state.get("origin") or "head-office", source_code=state.get("sourceCode"),
            source_name=state.get("sourceName"), reason=state.get("reason"),
            needed_by=date.fromisoformat(state["neededBy"]) if state.get("neededBy") else None,
            created_by_name=state.get("requestedBy"), sent_at=_dt(state.get("requestedAt")), sent_by_name=state.get("requestedBy"),
        )
        for line in state.get("lines") or []:
            product = await transfers_service._product_for({**line, "qtySent": line.get("qtyRequested")})
            await StockRequestLine.create(
                request=request, product=product, sku=line.get("sku") or product.sku, qty_requested=Decimal(str(line.get("qtyRequested") or "0")),
            )
        await request.fetch_related("lines")
        outcome = "created"
    else:
        outcome = "updated"
    # Withdrawn here while head office still had it waiting: the withdrawal is on its way up and stands.
    if not (request.status == "cancelled" and incoming == "sent"):
        request.status = incoming
    request.head_office_number = state.get("headOfficeNumber") or request.head_office_number
    request.received_at_head_office = _dt(state.get("receivedAt")) or request.received_at_head_office
    request.decided_at = _dt(state.get("decidedAt")) or request.decided_at
    request.decided_by_name = state.get("decidedBy") or request.decided_by_name
    request.decision_note = state.get("decisionNote") or request.decision_note
    request.transfer_id = state.get("transferId") or request.transfer_id
    request.transfer_number = state.get("transferNumber") or request.transfer_number
    if state.get("sourceName"):
        request.source_name = state["sourceName"]
    await request.save()
    approved = {str(l.get("sku")): l.get("qtyApproved") for l in state.get("lines") or []}
    for line in request.lines:
        value = approved.get(str(line.sku))
        if value is not None:
            line.qty_approved = Decimal(str(value))
            await line.save(update_fields=["qty_approved"])

    asker = [str(request.created_by_id)] if request.created_by_id else []
    if request.status != before and request.status == "approved":
        changed = any(l.qty_approved is not None and l.qty_approved != l.qty_requested for l in request.lines)
        await alerts_service.notify(
            "request.approved", f"Head office approved {request.number}" + (" with changes" if changed else ""),
            body=(f"It comes as {request.transfer_number}. " if request.transfer_number else "") + (request.decision_note or ""),
            link=LINK, audience_any=REQUESTERS, users=asker, tone="good", subject=("stock-request", str(request.id)),
        )
    if request.status != before and request.status == "declined":
        await alerts_service.notify(
            "request.declined", f"Head office declined {request.number}", body=request.decision_note,
            link=LINK, audience_any=REQUESTERS, users=asker, tone="bad", subject=("stock-request", str(request.id)),
        )
    return outcome
