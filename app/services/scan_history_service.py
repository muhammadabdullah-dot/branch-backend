"""Scan history: what the till sends about each bill line, and the Branch Manager's report of it.

The till never waits for this. It keeps what happened on each line in a queue and sends a batch every few seconds
(POST /sales/scan-events); a batch sent twice is kept once, by the till's own id for each event. Its times arrive as
"this long ago" rather than as its own clock, so a till whose clock is wrong still files events at the right time.

How a line ended: taken off ("removed"), held, or sold. A sale marks its bill's open lines sold when it is made, and a
line whose events arrive after the sale is matched to it when the report is read. A line still open with no sale
behind it, and nothing done to it for two hours, reads as never paid.
"""
from datetime import date, datetime, timedelta, timezone

from app.core.device_context import get_device_id
from app.core.pk_time import day_start
from app.models import Device, Product, SaleRecord, ScanEvent, ScanLine, User

ACTIONS = ("added", "more", "less", "level", "removed", "held")
# "slip": a Pharmacist's pharmacy slip paid at the cash counter. Its lines are filed by the server when the payment is
# taken (services/slips_service.py), under the person who took it, sold on that payment's bill.
HOWS = ("scan", "search", "weight", "recall", "slip")
# An open line with no sale and nothing done to it for this long is a bill that was never paid.
UNPAID_AFTER = timedelta(hours=2)
# An event can't be older than this: the till's queue is kept a week at most.
OLDEST = timedelta(days=7)


async def record(user: User, events: list) -> int:
    """Files a batch from the till, oldest first. Returns how many were new."""
    if not events:
        return 0
    now = datetime.now(timezone.utc)
    known = set(await ScanEvent.filter(id__in=[e.id for e in events]).values_list("id", flat=True))
    fresh = [e for e in events if e.id not in known and e.action in ACTIONS]
    if not fresh:
        return 0
    products = set(await Product.filter(id__in=list({e.productId for e in fresh})).values_list("id", flat=True))
    from app.services import till_service

    till = await till_service.session_for(user)
    device = get_device_id()
    open_lines: dict[tuple[str, int], ScanLine] = {}
    saved = 0
    for e in sorted(fresh, key=lambda e: -(e.ageMs or 0)):
        at = now - min(max(timedelta(milliseconds=e.ageMs or 0), timedelta(0)), OLDEST)
        key = (e.billId, e.lineKey)
        line = None
        if e.action != "added":
            line = open_lines.get(key) or await ScanLine.filter(bill_id=e.billId, line_key=e.lineKey, user_id=user.id).order_by("-first_at").first()
        if line is None:
            if e.productId not in products:
                continue
            line = await ScanLine.create(
                bill_id=e.billId, line_key=e.lineKey, product_id=e.productId, user=user, till_session=till,
                counter_id=till.counter_id if till else None, device_id=device, how=e.how if e.how in HOWS else "scan",
                first_at=at, last_at=at, qty=e.qty, level=e.level,
            )
        else:
            line.last_at = max(line.last_at, at)
            line.qty, line.level = e.qty, e.level
        if e.action in ("removed", "held"):
            line.outcome, line.ended_at = e.action, at
        await line.save()
        open_lines[key] = line
        await ScanEvent.create(id=e.id, line=line, at=at, action=e.action, qty=e.qty, level=e.level)
        saved += 1
    return saved


async def mark_sold(bill_id: str | None, invoice_number: str) -> None:
    """The bill was paid: every line still open on it was sold."""
    if bill_id:
        await ScanLine.filter(bill_id=bill_id, outcome="open").update(
            outcome="sold", ended_at=datetime.now(timezone.utc), invoice_number=invoice_number,
        )


async def report(day: date, user_id: str | None, show: str, viewer: User | None = None) -> dict:
    """The day's lines (Pakistan day), each with its events, how it ended and who, where and on what. `show` is
    "removed" (taken off, and never paid), "unpaid", "held", "sold" or "all". A viewer who doesn't sell Pharmacy Items
    sees those lines as "A pharmacy Item", never by name."""
    from app.services import pharmacy_service

    hide = pharmacy_service.hides_pharmacy(viewer)
    pharmacy_departments = await pharmacy_service.departments() if hide else set()
    qs = ScanLine.filter(first_at__gte=day_start(day), first_at__lt=day_start(day + timedelta(days=1)))
    everyone = await qs.values_list("user_id", flat=True)
    if user_id:
        qs = qs.filter(user_id=user_id)
    lines = await qs.order_by("-first_at").prefetch_related("product", "user", "counter", "till_session", "events")

    # Lines whose events arrived after their sale, and bills paid since: sold.
    open_bills = list({l.bill_id for l in lines if l.outcome == "open"})
    sold: dict[str, str] = {}
    for i in range(0, len(open_bills), 500):
        chunk = open_bills[i:i + 500]
        sold.update({cid: inv for cid, inv in await SaleRecord.filter(client_request_id__in=chunk).values_list("client_request_id", "invoice_number")})
    now = datetime.now(timezone.utc)
    rows = []
    counts = {"removed": 0, "unpaid": 0, "held": 0, "sold": 0, "open": 0}
    for line in lines:
        ended = line.outcome
        if ended == "open" and line.bill_id in sold:
            line.outcome, line.invoice_number, line.ended_at = "sold", sold[line.bill_id], line.last_at
            await line.save(update_fields=["outcome", "invoice_number", "ended_at"])
            ended = "sold"
        elif ended == "open" and now - line.last_at > UNPAID_AFTER:
            ended = "unpaid"
        counts[ended] = counts.get(ended, 0) + 1
        wanted = {"removed": ("removed", "unpaid"), "unpaid": ("unpaid",), "held": ("held",), "sold": ("sold",)}.get(show)
        if wanted and ended not in wanted:
            continue
        unseen = hide and pharmacy_service.is_pharmacy(line.product, pharmacy_departments)
        rows.append({
            "id": str(line.id), "billId": line.bill_id, "firstAt": line.first_at, "endedAt": line.ended_at if ended in ("removed", "held", "sold") else None,
            "userId": str(line.user_id), "userName": line.user.name, "counter": line.counter.name if line.counter else None,
            "till": line.till_session.session_number if line.till_session else None, "deviceId": line.device_id,
            "productId": pharmacy_service.FOLDED_ID if unseen else str(line.product_id), "sku": "" if unseen else line.product.sku,
            "name": "A pharmacy Item" if unseen else line.product.name,
            "qty": line.qty, "level": line.level, "how": line.how, "outcome": ended, "invoiceNumber": line.invoice_number,
            "events": [{"at": ev.at, "action": ev.action, "qty": ev.qty, "level": ev.level} for ev in sorted(line.events, key=lambda ev: ev.at)],
        })
    devices = {d.id: d.name for d in await Device.filter(id__in=list({r["deviceId"] for r in rows if r["deviceId"]}))} if rows else {}
    for row in rows:
        row["device"] = devices.get(row["deviceId"]) if row["deviceId"] else None
    names = {str(u.id): u.name for u in await User.filter(id__in=list(set(everyone)))} if everyone else {}
    people = sorted(({"id": uid, "name": name} for uid, name in names.items()), key=lambda p: p["name"].lower())
    return {"rows": rows, "people": people, "counts": counts}
