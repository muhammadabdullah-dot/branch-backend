"""What this branch tells head office about itself.

The branch computes its own aggregates and ships those, rather than shipping raw documents for the
Cloud to re-derive. Three reasons, in order of how much they matter:

1. **These are the figures the branch already stands behind.** The same folds drive the Branch
   App's own Reports screen. If Executive and Branch Reports ever disagree, that is a real bug —
   which is only true while both are computed from one place.
2. **The outbox cannot do this job.** It carries "a sale happened, invoice X, Rs Y" by design
   (contracts.md §3.5) — no line items, no cashier, no tender split. Per-product-per-day figures
   are simply not recoverable from it.
3. **Size.** 47,000 catalog rows against 865 product-days. A branch link in Multan notices the
   difference.

The SQL below is raw rather than ORM-built. These are wide aggregate folds over hundreds of
thousands of rows, and expressing them through the ORM would be slower, longer, and harder to check
against the Reports screen they have to agree with.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from tortoise import Tortoise

# Branch trading days are stored shifted so that stored-UTC + 5 is the branch's own wall clock.
# Converting here means the Cloud's hour-of-day profile reads as real shop hours (09:00-20:00)
# instead of as a UTC artefact nobody in the shop recognises.
PKT_OFFSET_HOURS = 5

# A dashboard is a glance. A real catalog can put thousands of items under the low-stock line, so
# only the most urgent travel — `totalOfKind` carries the true count alongside.
ALERTS_PER_KIND = 200
LOW_STOCK_THRESHOLD = Decimal("20")
NEAR_EXPIRY_DAYS = 30

# Item-level stock is the one genuinely large payload: one row per catalog product. Chunked so a
# dropped connection costs one chunk rather than the whole picture.
STOCK_CHUNK_SIZE = 2000


async def _q(sql: str) -> list[dict]:
    return await Tortoise.get_connection("default").execute_query_dict(sql)


def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return str(v)


def _s(v) -> str:
    return "0" if v is None else str(v)


async def build_aggregates() -> dict:
    """Everything the Cloud's Executive dashboard is built from, in the shape its apply layer
    expects. Keys are camelCase because this crosses a wire, not a module boundary."""
    daily = await _q("""
        SELECT date(at) AS day,
               COUNT(*)                     AS invoices,
               COALESCE(SUM(gross), 0)      AS gross_sales,
               COALESCE(SUM(disc_total), 0) AS disc_total,
               COALESCE(SUM(gst), 0)        AS gst,
               COALESCE(SUM(net_value), 0)  AS net_sales,
               COALESCE(SUM(cash_back), 0) AS cash_back,
               COUNT(DISTINCT CASE WHEN party_id IS NOT NULL THEN party_id END)     AS named_customers,
               MIN(at) AS first_sale_at, MAX(at) AS last_sale_at
        FROM sale_records GROUP BY date(at)
    """)
    # Items returned on a bill count against the day's items and cost, not towards them.
    items = {r["day"]: r for r in await _q("""
        SELECT date(s.at) AS day, COALESCE(SUM(CASE WHEN sl.is_return THEN -sl.qty ELSE sl.qty END), 0) AS items_sold
        FROM sale_lines sl JOIN sale_records s ON s.id = sl.sale_id GROUP BY date(s.at)
    """)}
    # Cost of goods at each line's own cost when it was sold (the Item's average cost then), falling back to today's
    # average only for sales from before that was kept.
    cogs = {r["day"]: r for r in await _q("""
        SELECT date(s.at) AS day,
               COALESCE(SUM((CASE WHEN sl.is_return THEN -sl.qty ELSE sl.qty END) * COALESCE(sl.unit_cost, p.avg_cost, 0)), 0) AS cogs
        FROM sale_lines sl JOIN sale_records s ON s.id = sl.sale_id
        JOIN products p ON p.id = sl.product_id GROUP BY date(s.at)
    """)}
    # Money by how it was paid: cash actually kept in the drawer (less change given), and credit put on account.
    # Card, wallet, bank, voucher and points payments are neither.
    tender_days = {r["day"]: r for r in await _q("""
        SELECT date(s.at) AS day,
               COALESCE(SUM(CASE WHEN t.code = 'CASH' THEN t.amount ELSE 0 END), 0) AS cash_tendered,
               COALESCE(SUM(CASE WHEN t.code = 'CREDIT' THEN t.amount ELSE 0 END), 0) AS credit_sales
        FROM sale_tenders t JOIN sale_records s ON s.id = t.sale_id GROUP BY date(s.at)
    """)}
    returns_by_day = {r["day"]: r for r in await _q("""
        SELECT date(at) AS day, COUNT(*) AS returns_count,
               COALESCE(SUM(refund_total), 0) AS returns_value
        FROM return_records GROUP BY date(at)
    """)}
    tills = {r["day"]: r for r in await _q("""
        SELECT date(closed_at) AS day, COUNT(*) AS tills_closed,
               COALESCE(SUM(variance), 0) AS till_variance
        FROM till_sessions WHERE closed_at IS NOT NULL GROUP BY date(closed_at)
    """)}
    cash = {r["day"]: r for r in await _q("""
        SELECT date(at) AS day,
               COALESCE(SUM(CASE WHEN kind = 'in'  THEN amount ELSE 0 END), 0) AS cash_in,
               COALESCE(SUM(CASE WHEN kind = 'out' THEN amount ELSE 0 END), 0) AS cash_out
        FROM cash_movements GROUP BY date(at)
    """)}
    staff = {r["day"]: r for r in await _q("""
        SELECT date(at) AS day, COUNT(DISTINCT cashier_id) AS staff_on_duty
        FROM sale_records GROUP BY date(at)
    """)}

    daily_out = []
    for r in daily:
        d = r["day"]
        daily_out.append({
            "day": d, "invoices": r["invoices"],
            "grossSales": _s(r["gross_sales"]), "discTotal": _s(r["disc_total"]), "gst": _s(r["gst"]),
            "netSales": _s(r["net_sales"]),
            "itemsSold": _s(items.get(d, {}).get("items_sold")),
            "cogs": _s(cogs.get(d, {}).get("cogs")),
            "returnsValue": _s(returns_by_day.get(d, {}).get("returns_value")),
            "returnsCount": returns_by_day.get(d, {}).get("returns_count", 0) or 0,
            "cashCollected": _s(Decimal(str(tender_days.get(d, {}).get("cash_tendered") or 0)) - Decimal(str(r["cash_back"] or 0))),
            "creditSales": _s(tender_days.get(d, {}).get("credit_sales")),
            "cashIn": _s(cash.get(d, {}).get("cash_in")), "cashOut": _s(cash.get(d, {}).get("cash_out")),
            "tillVariance": _s(tills.get(d, {}).get("till_variance")),
            "tillsClosed": tills.get(d, {}).get("tills_closed", 0) or 0,
            "staffOnDuty": staff.get(d, {}).get("staff_on_duty", 0) or 0,
            "firstSaleAt": _iso(r["first_sale_at"]), "lastSaleAt": _iso(r["last_sale_at"]),
            "namedCustomers": r["named_customers"] or 0,
        })

    cashiers = [{
        "day": r["day"], "cashierName": r["cashier_name"],
        "invoices": r["invoices"], "netSales": _s(r["net_sales"]),
    } for r in await _q("""
        SELECT date(s.at) AS day, u.name AS cashier_name,
               COUNT(*) AS invoices, COALESCE(SUM(s.net_value), 0) AS net_sales
        FROM sale_records s JOIN users u ON u.id = s.cashier_id
        GROUP BY date(s.at), u.name
    """)]

    products = [{
        "day": r["day"], "sku": r["sku"], "name": r["name"],
        "department": r["department"], "category": r["category"], "brand": r["brand"],
        "qty": _s(r["qty"]), "netSales": _s(r["net_sales"]), "cogs": _s(r["cogs"]),
    } for r in await _q("""
        SELECT date(s.at) AS day, p.sku, p.name, p.department, p.category, p.brand,
               COALESCE(SUM(CASE WHEN sl.is_return THEN -sl.qty ELSE sl.qty END), 0) AS qty,
               COALESCE(SUM((CASE WHEN sl.is_return THEN -sl.qty ELSE sl.qty END) * sl.unit_price - COALESCE(sl.disc_amount, 0)), 0) AS net_sales,
               COALESCE(SUM((CASE WHEN sl.is_return THEN -sl.qty ELSE sl.qty END) * COALESCE(sl.unit_cost, p.avg_cost, 0)), 0) AS cogs
        FROM sale_lines sl JOIN sale_records s ON s.id = sl.sale_id
        JOIN products p ON p.id = sl.product_id
        GROUP BY date(s.at), p.sku
    """)]

    # Who sold how much of each item, each day: the person is whoever rang the bill, as in `cashiers` above.
    product_cashiers = [{
        "day": r["day"], "sku": r["sku"], "cashierName": r["cashier_name"], "invoices": r["invoices"],
        "qty": _s(r["qty"]), "netSales": _s(r["net_sales"]), "cogs": _s(r["cogs"]),
    } for r in await _q("""
        SELECT date(s.at) AS day, p.sku, u.name AS cashier_name,
               COUNT(DISTINCT s.id) AS invoices,
               COALESCE(SUM(CASE WHEN sl.is_return THEN -sl.qty ELSE sl.qty END), 0) AS qty,
               COALESCE(SUM((CASE WHEN sl.is_return THEN -sl.qty ELSE sl.qty END) * sl.unit_price - COALESCE(sl.disc_amount, 0)), 0) AS net_sales,
               COALESCE(SUM((CASE WHEN sl.is_return THEN -sl.qty ELSE sl.qty END) * COALESCE(sl.unit_cost, p.avg_cost, 0)), 0) AS cogs
        FROM sale_lines sl JOIN sale_records s ON s.id = sl.sale_id
        JOIN products p ON p.id = sl.product_id
        JOIN users u ON u.id = s.cashier_id
        GROUP BY date(s.at), p.sku, u.name
    """)]

    hourly = [{
        "day": r["day"], "hour": (int(r["utc_hour"]) + PKT_OFFSET_HOURS) % 24,
        "invoices": r["invoices"], "netSales": _s(r["net_sales"]),
    } for r in await _q("""
        SELECT date(at) AS day, cast(strftime('%H', at) AS int) AS utc_hour,
               COUNT(*) AS invoices, COALESCE(SUM(net_value), 0) AS net_sales
        FROM sale_records GROUP BY date(at), utc_hour
    """)]

    till_closes = [{
        "day": r["day"], "sessionNumber": r["session_number"], "cashierName": r["cashier_name"] or "-",
        "counterName": r["counter_name"],
        "openedAt": _iso(r["opened_at"]), "closedAt": _iso(r["closed_at"]),
        "openingFloat": _s(r["opening_float"]), "netCash": _s(r["net_cash"]),
        "countedCash": _s(r["counted_cash"]), "variance": _s(r["variance"]),
    } for r in await _q("""
        SELECT date(s.closed_at) AS day, s.session_number, u.name AS cashier_name, c.name AS counter_name,
               s.opened_at, s.closed_at, s.opening_float, s.net_cash, s.counted_cash, s.variance
        FROM till_sessions s LEFT JOIN users u ON u.id = s.opened_by_id
        LEFT JOIN sales_counters c ON c.id = s.counter_id
        WHERE s.closed_at IS NOT NULL
    """)]

    # Who was put on which counter, and for how long. This is the one figure head office cannot work
    # out backwards from sales: somebody on the floor all morning who sold nothing is invisible to
    # every other row in this snapshot.
    duties = [{
        "day": r["day"], "cashierName": r["cashier_name"] or "-", "counterName": r["counter_name"],
        "spells": r["spells"], "minutes": int(r["minutes"] or 0),
    } for r in await _q("""
        SELECT date(d.started_at) AS day, u.name AS cashier_name, c.name AS counter_name,
               COUNT(*) AS spells,
               SUM((julianday(COALESCE(d.ended_at, CURRENT_TIMESTAMP)) - julianday(d.started_at)) * 1440) AS minutes
        FROM counter_duties d JOIN users u ON u.id = d.user_id
        JOIN sales_counters c ON c.id = d.counter_id
        GROUP BY date(d.started_at), u.name, c.name
    """)]

    tenders = [{
        "day": r["day"], "code": r["code"], "name": r["name"],
        "uses": r["uses"], "amount": _s(r["amount"]),
    } for r in await _q("""
        SELECT date(s.at) AS day, t.code, COALESCE(m.name, t.code) AS name,
               COUNT(*) AS uses, COALESCE(SUM(t.amount), 0) AS amount
        FROM sale_tenders t JOIN sale_records s ON s.id = t.sale_id
        LEFT JOIN payment_methods m ON m.code = t.code
        GROUP BY date(s.at), t.code
    """)]

    overrides = [{
        "day": r["day"], "invoiceNumber": r["invoice_number"], "at": _iso(r["at"]),
        "cashierName": r["cashier_name"], "approvedBy": r["approved_by"],
        "gross": _s(r["gross"]), "discTotal": _s(r["disc_total"]), "netValue": _s(r["net_value"]),
    } for r in await _q("""
        SELECT date(s.at) AS day, s.invoice_number, s.at, c.name AS cashier_name,
               a.name AS approved_by, s.gross, s.disc_total, s.net_value
        FROM sale_records s
        LEFT JOIN users c ON c.id = s.cashier_id
        LEFT JOIN users a ON a.id = s.discount_override_by_id
        WHERE s.discount_override_by_id IS NOT NULL
    """)]

    return_rows = [{
        "day": r["day"], "at": _iso(r["at"]), "againstInvoice": r["against_invoice"],
        "cashierName": r["cashier_name"], "refundTotal": _s(r["refund_total"]),
        "productName": r["product_name"], "productSku": r["product_sku"], "qty": _s(r["qty"]),
    } for r in await _q("""
        SELECT date(r.at) AS day, r.at, s.invoice_number AS against_invoice,
               u.name AS cashier_name, r.refund_total,
               p.name AS product_name, p.sku AS product_sku, rl.qty
        FROM return_records r
        LEFT JOIN sale_records s ON s.id = r.against_id
        LEFT JOIN users u ON u.id = r.cashier_id
        LEFT JOIN return_lines rl ON rl.return_record_id = r.id
        LEFT JOIN products p ON p.id = rl.product_id
    """)]

    credit = [{
        "code": r["code"], "name": r["name"], "phone": r["phone"], "tier": r["tier"],
        "creditLimit": _s(r["credit_limit"]), "creditBalance": _s(r["credit_balance"]),
    } for r in await _q("""
        SELECT code, name, phone, tier, credit_limit, credit_balance
        FROM parties WHERE credit_allowed = 1 AND is_walk_in = 0
    """)]

    stock_value = (await _q("""
        SELECT COALESCE(SUM(b.qty * p.price), 0) AS stock_value
        FROM (SELECT product_id, SUM(qty) AS qty FROM stock_movements
              GROUP BY product_id HAVING SUM(qty) != 0) b
        JOIN products p ON p.id = b.product_id
    """))[0]["stock_value"]

    return {
        "daily": daily_out, "cashiers": cashiers, "products": products, "productCashiers": product_cashiers, "hourly": hourly,
        "tillCloses": till_closes, "duties": duties, "tenders": tenders, "overrides": overrides,
        "returns": return_rows, "creditCustomers": credit,
        "alerts": await build_alerts(), "stockValue": _s(stock_value),
    }


async def build_alerts() -> list[dict]:
    """Stock exceptions, ranked and trimmed here because this is the only side that can see the
    whole catalog. `totalOfKind` carries the real count so the Cloud never implies 200 is all."""
    balances = await _q("""
        SELECT p.sku, p.name, b.qty
        FROM (SELECT product_id, SUM(qty) AS qty FROM stock_movements GROUP BY product_id) b
        JOIN products p ON p.id = b.product_id
    """)
    out_of_stock, low_stock = [], []
    for r in balances:
        qty = Decimal(str(r["qty"] or 0))
        if qty <= 0:
            out_of_stock.append({"sku": r["sku"], "name": r["name"], "qty": qty, "expiry": None, "detail": None})
        elif qty < LOW_STOCK_THRESHOLD:
            low_stock.append({"sku": r["sku"], "name": r["name"], "qty": qty, "expiry": None, "detail": f"{qty} left"})

    now = datetime.now(timezone.utc)
    near, expired = [], []
    for r in await _q("""
        SELECT p.sku, p.name, bt.expiry, bt.received_qty
        FROM batches bt JOIN products p ON p.id = bt.product_id WHERE bt.expiry IS NOT NULL
    """):
        raw = r["expiry"]
        exp = raw if isinstance(raw, datetime) else None
        if exp is None:
            try:
                exp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        days = (exp - now).days
        if days >= 0 and days > NEAR_EXPIRY_DAYS:
            continue  # plenty of shelf life — not an exception
        row = {"sku": r["sku"], "name": r["name"], "qty": Decimal(str(r["received_qty"] or 0)), "expiry": exp,
               "detail": f"expires in {days} days" if days >= 0 else f"expired {abs(days)} days ago"}
        (expired if days < 0 else near).append(row)

    low_stock.sort(key=lambda r: r["qty"])
    near.sort(key=lambda r: r["expiry"])
    expired.sort(key=lambda r: r["expiry"])

    out: list[dict] = []
    for kind, rows in (("out-of-stock", out_of_stock), ("low-stock", low_stock),
                       ("near-expiry", near), ("expired", expired)):
        for r in rows[:ALERTS_PER_KIND]:
            out.append({"kind": kind, "sku": r["sku"], "name": r["name"], "qty": _s(r["qty"]),
                        "expiry": _iso(r["expiry"]), "detail": r["detail"], "totalOfKind": len(rows)})
    return out


async def stock_rows(product_ids: list[str] | None = None) -> list[dict]:
    """One row per catalog product (or just `product_ids`): what is on hand, what it cost, what it sells
    for, when it last moved — and where it came from and went (see stock_provenance_service).

    `last_sold_at` is the field the whole dead-stock question turns on, and it is computed here
    rather than on the Cloud on purpose — the branch holds the complete sales history, while the
    Cloud only ever sees the window of daily stats it has been sent. A product last sold eighteen
    months ago is invisible to the Cloud's own data and obvious to this query.
    """
    from app.services import stock_provenance_service

    sql = """
        SELECT p.id, p.sku, p.name, p.department, p.category, p.brand,
               p.avg_cost, p.price,
               COALESCE(b.qty, 0)            AS qty,
               s.last_sold_at, s.units_sold, s.days_with_sales,
               g.last_received_at
        FROM products p
        LEFT JOIN (SELECT product_id, SUM(qty) AS qty FROM stock_movements GROUP BY product_id) b
               ON b.product_id = p.id
        LEFT JOIN (SELECT sl.product_id,
                          MAX(sr.at)                  AS last_sold_at,
                          SUM(sl.qty)                 AS units_sold,
                          COUNT(DISTINCT date(sr.at)) AS days_with_sales
                   FROM sale_lines sl JOIN sale_records sr ON sr.id = sl.sale_id
                   GROUP BY sl.product_id) s ON s.product_id = p.id
        LEFT JOIN (SELECT product_id, MAX(at) AS last_received_at
                   FROM stock_movements WHERE qty > 0 GROUP BY product_id) g ON g.product_id = p.id
        {where}
    """
    conn = Tortoise.get_connection("default")
    if product_ids is None:
        found = await conn.execute_query_dict(sql.format(where=""))
    else:
        found = []
        for i in range(0, len(product_ids), 500):
            chunk = product_ids[i:i + 500]
            found += await conn.execute_query_dict(sql.format(where=f"WHERE p.id IN ({','.join('?' * len(chunk))})"), chunk)
    detail = await stock_provenance_service.detail(product_ids)
    out = []
    for r in found:
        extra = detail.get(r["id"]) or {}
        out.append({
            "sku": r["sku"], "name": r["name"], "department": r["department"],
            "category": r["category"], "brand": r["brand"],
            "qty": _s(r["qty"]), "avgCost": _s(r["avg_cost"]), "price": _s(r["price"]),
            "lastSoldAt": _iso(r["last_sold_at"]), "lastReceivedAt": _iso(r["last_received_at"]),
            "unitsSold": _s(r["units_sold"]), "daysWithSales": r["days_with_sales"] or 0,
            "flows": extra.get("flows") or {}, "origin": extra.get("origin") or {},
            "locations": extra.get("locations") or [], "lastIn": extra.get("lastIn"),
            "lastMovedAt": extra.get("lastMovedAt"),
        })
    return out


async def changed_product_ids(after_rowid: int, after: datetime | None) -> tuple[list[str], int]:
    """Products whose stock moved (or whose price changed) since the last time head office was told,
    and the movement row it was told up to."""
    conn = Tortoise.get_connection("default")
    mark = (await conn.execute_query_dict("SELECT COALESCE(MAX(rowid), 0) AS m FROM stock_movements"))[0]["m"]
    ids = {r["product_id"] for r in await conn.execute_query_dict(
        "SELECT DISTINCT product_id FROM stock_movements WHERE rowid > ? AND rowid <= ?", [after_rowid, mark],
    )}
    if after is not None:
        ids |= {r["product_id"] for r in await conn.execute_query_dict(
            "SELECT DISTINCT product_id FROM product_price_changes WHERE at > ?", [after.isoformat(sep=" ")],
        )}
    return sorted(ids), int(mark)


async def movement_mark() -> int:
    rows = await Tortoise.get_connection("default").execute_query_dict("SELECT COALESCE(MAX(rowid), 0) AS m FROM stock_movements")
    return int(rows[0]["m"])


def chunked(rows: list[dict], size: int = STOCK_CHUNK_SIZE):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def new_snapshot_id() -> str:
    return uuid.uuid4().hex
