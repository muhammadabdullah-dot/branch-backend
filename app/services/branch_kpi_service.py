"""Branch figures you can open up: every dashboard figure down to the day, the Item, the person and the bill.

The server shapes every level, the way head office's executive views do: a headline, an optional series, a table
whose columns say where a cell links to, further sections, and a note on how the figure is worked out. One screen in
the app renders all of it, which is what lets a figure open into a day, the day into an Item, the Item into the
people who sold it and down to a single bill without a screen per figure.

Every view is the period's trade narrowed by whatever the reader has picked on the way down (a day, a department,
an Item, a person), so the numbers at each level add up to the level above. The order they picked things in travels
in `path`, which is how the breadcrumb climbs back up the way they came.

Figures are added up in SQL from bills, bill lines, tenders, the Returns screen, till closes and the stock ledger,
using the same line arithmetic as ABC and XYZ (see branch_analytics_service.lines_sql).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.core.pk_time import pk_day, pk_time
from app.services import branch_analytics_service as an
from app.services import pharmacy_service
from app.services.branch_analytics_service import (
    SQL_DAY,
    SQL_HOUR,
    AnalysisError,
    Period,
    Scope,
    as_day,
    day_label,
    f,
    iso_at,
    money,
    pct,
    q,
    units,
)

# Low stock is fewer than the Item's own reorder level, or the branch's usual low stock level for an Item without one
# (Lists and Settings > Receipt and Vouchers). The snapshot to head office flags Items by the same rule.
LOW_STOCK_THRESHOLD = 20  # the usual level until a branch sets its own
LOW_BELOW = "COALESCE(CAST(p.reorder_level AS REAL), ?)"
NEAR_EXPIRY_DAYS = 30
NOT_SOLD_DAYS = 90
TABLE_LIMIT = 300

GROUP_ORDER = ["Sales", "Money", "Mix", "People", "Stock"]

# id: (label, group, what it means, what opening it shows)
KPIS: dict[str, tuple[str, str, str, str]] = {
    "sales": ("Sales", "Sales", "What the branch sold after discounts, less returns, before GST.", "See each day"),
    "gross-profit": ("Gross profit", "Sales", "Sales less what the goods cost when they were sold.", "See the margin"),
    "bills": ("Bills", "Sales", "How many bills were rung.", "See each day"),
    "average-bill": ("Average bill", "Sales", "What a customer paid on one bill, on average, GST included.", "See the biggest bills"),
    "items-per-bill": ("Items per bill", "Sales", "Units on a bill, on average. Weighed goods count by their weight.", "See the fullest bills"),
    "returns": ("Returns", "Sales", "Goods that came back, at what they sold for before GST: handed back on a bill or refunded on the Returns screen.", "See each return"),
    "discounts": ("Discounts", "Sales", "Money taken off bills, and bills whose discount was above the salesperson's own limit.", "See who gave them"),
    "payment-mix": ("Payment mix", "Money", "How customers paid: cash (after change), card, wallets, credit and vouchers.", "See each method"),
    "busy-hours": ("Busy hours", "Money", "When the bills are rung, hour by hour.", "See every hour"),
    "tills": ("Counters and tills", "Money", "Tills closed in the period and how far the counted cash was from what was expected.", "See each till close"),
    "departments": ("Sales by department", "Mix", "Which departments the sales came from.", "See every department"),
    "categories": ("Sales by category", "Mix", "Which categories the sales came from.", "See every category"),
    "brands": ("Sales by brand", "Mix", "Which brands the sales came from.", "See every brand"),
    "top-items": ("Top Items", "Mix", "The Items that brought in the most sales.", "See the ranking"),
    "bottom-items": ("Bottom Items", "Mix", "Items that sold, but least of all.", "See the slowest"),
    "staff": ("Staff", "People", "Sales, bills and average bill for each person who rang bills.", "See each person"),
    "stock-value": ("Stock value at cost", "Stock", "Everything on hand now, at the Items' average cost.", "See where it sits"),
    "low-stock": ("Low stock", "Stock", "Items with some stock left but fewer than their reorder level (or the branch's usual low stock level).", "See the list"),
    "near-expiry": ("Near expiry", "Stock", f"Batches of Items in stock that expire within {NEAR_EXPIRY_DAYS} days, or already have.", "See the batches"),
    "not-sold-90": ("Not sold for 90 days", "Stock", f"Items with stock on hand that haven't sold for {NOT_SOLD_DAYS} days (counted from when they came in, if they never have).", "See the Items"),
    "day": ("Day", "Sales", "Sales on this shop day after discounts, less returns, before GST.", ""),
    "group": ("Group", "Mix", "Sales of the Items in this group after discounts, less returns, before GST.", ""),
    "item": ("Item", "Mix", "What this Item sold for after discounts, less returns, before GST.", ""),
    "person": ("Person", "People", "What this person sold: the bills they rang, less refunds they gave, before GST.", ""),
    "item-person": ("Item and person", "People", "What this person sold of this Item, before GST.", ""),
    "bill": ("Bill", "Sales", "What the customer paid on this bill, GST included.", ""),
}
BOARD = [k for k in KPIS if k not in ("day", "group", "item", "person", "item-person", "bill")]
GROUP_KPI = {"departments": "department", "categories": "category", "brands": "brand"}
GROUP_WORD = {"department": "department", "category": "category", "brand": "brand"}

# The order a trail climbs in when the URL doesn't say (a link typed by hand): most people go day, then Item, then person.
DEFAULT_PATH = ["day", "group", "item", "person"]
STEP_KEYS = {"day": ("day",), "group": ("groupKind", "group"), "item": ("productId",), "person": ("userId",)}


# ── formatting ──────────────────────────────────────────────────────────────────────────────────

def rs(value) -> str:
    n = round(f(value))
    return f"-Rs {abs(n):,}" if n < 0 else f"Rs {n:,}"


def count(value) -> str:
    return f"{int(value or 0):,}"


def qty_text(value) -> str:
    v = round(f(value), 2)
    return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"


def unit_count(value) -> str:
    return f"{qty_text(value)} {'unit' if round(f(value), 2) == 1 else 'units'}"


def hour_label(hour: int) -> str:
    def t(h: int) -> str:
        h %= 24
        return f"{12 if h % 12 == 0 else h % 12} {'am' if h < 12 else 'pm'}"
    return f"{t(hour)} to {t(hour + 1)}"


def plural(n, one: str, many: str | None = None) -> str:
    return f"{count(n)} {one if int(n or 0) == 1 else (many or one + 's')}"


def group_label(kind: str, value: str | None) -> str:
    return value if value and value != an.NO_GROUP else f"No {GROUP_WORD.get(kind, kind)}"


# ── the wire shapes ─────────────────────────────────────────────────────────────────────────────

def delta(cur: float, prev: float, good_when_up: bool | None = True) -> dict | None:
    if not prev:
        return None
    change = (cur - prev) / abs(prev) * 100
    direction = "up" if change > 0.05 else ("down" if change < -0.05 else "flat")
    good = None if good_when_up is None or direction == "flat" else ((direction == "up") == good_when_up)
    return {"value": money(cur - prev), "percent": round(change, 1), "direction": direction, "good": good, "label": "on the period before"}


def kpi(kpi_id: str, value, display: str, sub: str | None = None, change: dict | None = None,
        severity: str | None = None, unit: str = "") -> dict:
    label, group, hint, cta = KPIS[kpi_id]
    return {
        "id": kpi_id, "label": label, "group": group, "hint": hint, "value": None if value is None else str(value),
        "display": display, "unit": unit, "sub": sub, "delta": change, "severity": severity, "ctaLabel": cta,
    }


def col(key: str, label: str, fmt: str = "text", link: str | None = None, params: dict | None = None,
        carry: bool = True, link_key: str | None = None, align: str | None = None) -> dict:
    return {
        "key": key, "label": label, "format": fmt,
        "align": align or ("right" if fmt in ("money", "number", "percent") else "left"),
        "linkTo": link, "linkToKey": link_key, "linkParams": params, "linkCarry": carry,
    }


def section(title: str, columns: list[dict], rows: list[dict], empty: str, subtitle: str | None = None,
            action: dict | None = None, facts: bool = False) -> dict:
    """`facts` marks a what / detail list about one thing (a bill, an Item), shown as a list rather than a table."""
    return {"title": title, "subtitle": subtitle, "columns": columns, "rows": rows, "emptyText": empty, "action": action,
            "kind": "facts" if facts else "table"}


ITEM_LINK = {"productId": "productId"}
PERSON_LINK = {"userId": "userId"}
DAY_LINK = {"day": "day"}
BILL_LINK = {"invoice": "invoice"}


@dataclass
class Ctx:
    period: Period
    root: str
    day: date | None = None
    product_id: str | None = None
    user_id: str | None = None
    group_kind: str | None = None
    group: str | None = None
    invoice: str | None = None
    path: list[str] = field(default_factory=list)
    # A reader who doesn't sell Pharmacy Items (a Salesperson with this tick): a bill's Pharmacy Items reach them as one
    # line, never by name (services/pharmacy_service.py).
    hide_pharmacy: bool = False
    _users: dict | None = None
    _names: dict = field(default_factory=dict)

    def scope(self, **drop) -> Scope:
        s = Scope(day=self.day, product_id=self.product_id, user_id=self.user_id)
        if self.group_kind and self.group is not None:
            setattr(s, self.group_kind, self.group)
        for key in drop:
            setattr(s, key, None)
        return s

    def focus(self) -> dict:
        out = {"day": self.day.isoformat() if self.day else None, "productId": self.product_id, "userId": self.user_id,
               "groupKind": self.group_kind if self.group is not None else None, "group": self.group, "invoice": self.invoice}
        return {k: v for k, v in out.items() if v is not None}

    async def users(self) -> dict[str, str]:
        if self._users is None:
            self._users = {str(r["id"]): r["name"] for r in await q("SELECT id, name FROM users")}
        return self._users

    async def item_name(self, product_id: str) -> str:
        if product_id not in self._names:
            rows = await q("SELECT name, sku FROM products WHERE id = ?", [product_id])
            if not rows:
                raise AnalysisError("That Item isn't in this branch's catalog.")
            self._names[product_id] = rows[0]["name"]
        return self._names[product_id]


def _view_for(focus: dict) -> str:
    if "productId" in focus and "userId" in focus:
        return "item-person"
    if "productId" in focus:
        return "item"
    if "userId" in focus:
        return "person"
    if "group" in focus:
        return "group"
    return "day"


async def _step_label(ctx: Ctx, step: str) -> str:
    if step == "day":
        return day_label(ctx.day, year=False)
    if step == "group":
        return group_label(ctx.group_kind or "department", ctx.group)
    if step == "item":
        return await ctx.item_name(ctx.product_id)
    users = await ctx.users()
    return users.get(ctx.user_id, "Someone no longer on the staff list")


def _steps(ctx: Ctx) -> list[str]:
    present = {"day": ctx.day is not None, "group": ctx.group is not None, "item": ctx.product_id is not None, "person": ctx.user_id is not None}
    ordered = [s for s in ctx.path if s in present and present[s]]
    return list(dict.fromkeys(ordered + [s for s in DEFAULT_PATH if present[s]]))


async def _trail(ctx: Ctx, view: str) -> tuple[list[dict], str | None]:
    """Breadcrumbs back up the way the reader came, and the label of where they are now."""
    crumbs: list[dict] = []
    if view != ctx.root and ctx.root in BOARD:
        crumbs.append({"label": KPIS[ctx.root][0], "kpi": ctx.root, "focus": {}})
    steps = _steps(ctx)
    focus: dict = {}
    here = None
    for i, step in enumerate(steps):
        for key in STEP_KEYS[step]:
            focus[key] = ctx.focus()[key]
        label = await _step_label(ctx, step)
        if i == len(steps) - 1 and not ctx.invoice:
            here = label
        else:
            crumbs.append({"label": label, "kpi": _view_for(focus), "focus": {**focus, "path": ".".join(steps[:i + 1])}})
    if ctx.invoice:
        here = f"Bill {ctx.invoice}"
    return crumbs, here


async def _detail(ctx: Ctx, kpi_id: str, headline: dict, *, series: list[dict] | None = None, series_label: str | None = None,
                  series_kind: str = "line", columns: list[dict] | None = None, rows: list[dict] | None = None,
                  table_title: str | None = None, empty: str = "Nothing in this period.", sections: list[dict] | None = None,
                  notes: list[str] | None = None, label: str | None = None) -> dict:
    trail, here = await _trail(ctx, kpi_id)
    name, group, hint, _ = KPIS[kpi_id]
    return {
        "id": kpi_id, "label": label or name, "group": group, "hint": hint,
        "root": ctx.root, "focus": ctx.focus(), "path": _steps(ctx) + (["bill"] if ctx.invoice else []),
        "focusLabel": here, "trail": trail,
        # A view narrowed to one day says so, instead of the period it was opened from.
        "scopeLabel": day_label(ctx.day) if ctx.day and kpi_id != "bill" else None,
        "headline": headline, "period": ctx.period.out(),
        "seriesLabel": series_label, "seriesKind": series_kind, "series": series or [],
        "columns": columns or [], "rows": rows or [], "emptyText": empty, "tableTitle": table_title,
        "sections": sections or [], "notes": notes or [],
    }


# ── building blocks ─────────────────────────────────────────────────────────────────────────────

LINE_NOTE = "Sales are after discounts and before GST, less goods handed back on a bill and refunds taken on the Returns screen. A refund counts on the day it was given and against the person who gave it."
COST_NOTE = "Cost is each line's cost when it was sold; older sales use the Item's average cost."


async def totals(period: Period, scope: Scope) -> dict:
    sql, params = an.lines_sql(period, scope)
    row = (await q(f"""
        WITH l AS ({sql})
        SELECT COALESCE(SUM(sales), 0) AS sales, COALESCE(SUM(cost), 0) AS cost,
               COALESCE(SUM(CASE WHEN returned = 0 THEN qty ELSE 0 END), 0) AS units,
               COALESCE(SUM(CASE WHEN returned = 1 THEN -sales ELSE 0 END), 0) AS returns,
               COALESCE(SUM(CASE WHEN returned = 1 THEN -qty ELSE 0 END), 0) AS returned_units,
               COUNT(DISTINCT CASE WHEN source = 'bill' AND returned = 0 THEN sale_id END) AS bills,
               COUNT(DISTINCT product_id) AS items
        FROM l
    """, params))[0]
    return {k: f(v) for k, v in row.items()}


def bill_where(period: Period, ctx_day: date | None = None, user_id: str | None = None, alias: str = "s") -> tuple[str, list]:
    start, end = (an.bound(ctx_day), an.bound(ctx_day + timedelta(days=1))) if ctx_day else (period.start_at, period.end_at)
    where, params = [f"{alias}.at >= ?", f"{alias}.at < ?"], [start, end]
    if user_id:
        where.append(f"{alias}.cashier_id = ?")
        params.append(user_id)
    return " AND ".join(where), params


async def bill_totals(period: Period, day: date | None = None, user_id: str | None = None) -> dict:
    where, params = bill_where(period, day, user_id)
    row = (await q(f"""
        SELECT COUNT(*) AS bills, COALESCE(SUM(CAST(s.gross AS REAL)), 0) AS gross,
               COALESCE(SUM(CAST(s.disc_total AS REAL)), 0) AS discount, COALESCE(SUM(CAST(s.net_value AS REAL)), 0) AS paid,
               COALESCE(SUM(CAST(s.gst AS REAL)), 0) AS gst, COALESCE(SUM(CAST(s.cash_back AS REAL)), 0) AS cash_back,
               COALESCE(SUM(CASE WHEN s.discount_override_by_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS approvals,
               COALESCE(SUM(CASE WHEN CAST(s.disc_total AS REAL) > 0 THEN 1 ELSE 0 END), 0) AS discounted,
               COUNT(DISTINCT s.cashier_id) AS people
        FROM sale_records s WHERE {where}
    """, params))[0]
    return {k: f(v) for k, v in row.items()}


async def by_day(period: Period, scope: Scope) -> list[dict]:
    sql, params = an.lines_sql(period, scope)
    return await q(f"""
        WITH l AS ({sql})
        SELECT {SQL_DAY} AS day, SUM(sales) AS sales, SUM(cost) AS cost,
               SUM(CASE WHEN returned = 0 THEN qty ELSE 0 END) AS units,
               SUM(CASE WHEN returned = 1 THEN -sales ELSE 0 END) AS returns,
               COUNT(DISTINCT CASE WHEN source = 'bill' AND returned = 0 THEN sale_id END) AS bills
        FROM l GROUP BY day ORDER BY day
    """, params)


def fill_days(period: Period, rows: list[dict], key: str) -> list[dict]:
    """A point for every day of the period, so a quiet day shows as a dip and not as a gap the line jumps over."""
    got = {r["day"]: f(r[key]) for r in rows}
    days = min(period.days, 400)
    start = period.end - timedelta(days=days - 1)
    return [{"day": (start + timedelta(days=i)).isoformat(), "value": money(got.get((start + timedelta(days=i)).isoformat(), 0))} for i in range(days)]


async def by_item(period: Period, scope: Scope, order: str = "sales DESC", limit: int = TABLE_LIMIT, having: str = "") -> list[dict]:
    sql, params = an.lines_sql(period, scope)
    rows = await q(f"""
        WITH l AS ({sql})
        SELECT l.product_id AS product_id, p.sku AS sku, p.name AS name, p.department AS department, p.category AS category,
               p.brand AS brand, SUM(l.sales) AS sales, SUM(l.cost) AS cost, SUM(l.qty) AS net_units,
               SUM(CASE WHEN l.returned = 0 THEN l.qty ELSE 0 END) AS units,
               COUNT(DISTINCT CASE WHEN l.source = 'bill' AND l.returned = 0 THEN l.sale_id END) AS bills,
               MAX(CASE WHEN l.returned = 0 THEN l.at END) AS last_at
        FROM l JOIN products p ON p.id = l.product_id
        GROUP BY l.product_id {having}
        ORDER BY {order} LIMIT ?
    """, params + [limit])
    return rows


def item_row(r: dict, total_sales: float | None = None) -> dict:
    sales, cost = f(r["sales"]), f(r["cost"])
    return {
        "productId": str(r["product_id"]), "item": r["name"], "sku": r["sku"], "department": r.get("department"),
        "units": units(r["units"]), "netUnits": units(r.get("net_units", r["units"])), "sales": money(sales),
        "profit": money(sales - cost), "margin": pct(sales - cost, sales) if sales > 0 else None,
        "bills": int(r["bills"] or 0), "share": pct(sales, total_sales) if total_sales else None,
        "lastSold": iso_at(r.get("last_at")),
    }


async def by_person(period: Period, scope: Scope) -> list[dict]:
    sql, params = an.lines_sql(period, scope)
    rows = await q(f"""
        WITH l AS ({sql})
        SELECT user_id, SUM(sales) AS sales, SUM(cost) AS cost, SUM(CASE WHEN returned = 0 THEN qty ELSE 0 END) AS units,
               COUNT(DISTINCT CASE WHEN source = 'bill' AND returned = 0 THEN sale_id END) AS bills,
               SUM(CASE WHEN source = 'returns' THEN -sales ELSE 0 END) AS refunds
        FROM l GROUP BY user_id ORDER BY sales DESC
    """, params)
    return rows


async def by_group(period: Period, scope: Scope, kind: str) -> list[dict]:
    sql, params = an.lines_sql(period, scope)
    return await q(f"""
        WITH l AS ({sql})
        SELECT COALESCE(NULLIF(p.{kind}, ''), '{an.NO_GROUP}') AS grp, SUM(l.sales) AS sales, SUM(l.cost) AS cost,
               SUM(CASE WHEN l.returned = 0 THEN l.qty ELSE 0 END) AS units, COUNT(DISTINCT l.product_id) AS items,
               COUNT(DISTINCT CASE WHEN l.source = 'bill' AND l.returned = 0 THEN l.sale_id END) AS bills
        FROM l JOIN products p ON p.id = l.product_id
        GROUP BY grp ORDER BY sales DESC
    """, params)


async def bills_in(period: Period, scope: Scope, limit: int = TABLE_LIMIT, order: str = "at DESC") -> list[dict]:
    """The bills the scope's lines are on, with what of the scope each carried."""
    sql, params = an.lines_sql(period, scope)
    users = {str(r["id"]): r["name"] for r in await q("SELECT id, name FROM users")}
    rows = await q(f"""
        WITH l AS ({sql})
        SELECT l.sale_id AS sale_id, l.invoice AS invoice, MIN(l.at) AS at, l.user_id AS user_id,
               SUM(CASE WHEN l.returned = 0 THEN l.qty ELSE 0 END) AS units, SUM(l.sales) AS sales,
               CAST(s.net_value AS REAL) AS total, CAST(s.disc_total AS REAL) AS discount
        FROM l JOIN sale_records s ON s.id = l.sale_id
        WHERE l.source = 'bill'
        GROUP BY l.sale_id ORDER BY {order} LIMIT ?
    """, params + [limit])
    return [{
        "invoice": r["invoice"], "at": iso_at(r["at"]), "userId": str(r["user_id"]),
        "person": users.get(str(r["user_id"]), "-"), "units": units(r["units"]), "sales": money(r["sales"]),
        "total": money(r["total"]), "discount": money(r["discount"]),
    } for r in rows]


BILL_COLUMNS = [
    col("invoice", "Bill", "mono", link="bill", params=BILL_LINK),
    col("at", "When", "datetime"),
    col("person", "Rung by", link="person", params=PERSON_LINK, carry=False),
    col("units", "Units", "number"),
    col("sales", "Sales", "money"),
    col("total", "Bill total", "money"),
]


def day_columns(link_kpi: str = "day", with_profit: bool = True, carry: bool = True) -> list[dict]:
    cols = [col("day", "Day", "date", link=link_kpi, params=DAY_LINK, carry=carry), col("bills", "Bills", "number"),
            col("units", "Units", "number"), col("sales", "Sales", "money")]
    if with_profit:
        cols += [col("profit", "Gross profit", "money"), col("margin", "Margin", "percent")]
    return cols + [col("returns", "Returns", "money")]


def day_row(r: dict) -> dict:
    sales, cost = f(r["sales"]), f(r["cost"])
    return {"day": r["day"], "bills": int(r["bills"] or 0), "units": units(r["units"]), "sales": money(sales),
            "profit": money(sales - cost), "margin": pct(sales - cost, sales) if sales > 0 else None, "returns": money(r["returns"])}


async def person_rows(period: Period, scope: Scope, ctx: Ctx) -> list[dict]:
    users = await ctx.users()
    rows = await by_person(period, scope)
    total = sum(f(r["sales"]) for r in rows if f(r["sales"]) > 0)
    return [{
        "userId": str(r["user_id"]), "person": users.get(str(r["user_id"]), "Someone no longer on the staff list"),
        "bills": int(r["bills"] or 0), "units": units(r["units"]), "sales": money(r["sales"]),
        "average": money(f(r["sales"]) / r["bills"]) if r["bills"] else None,
        "profit": money(f(r["sales"]) - f(r["cost"])), "refunds": money(r["refunds"]), "share": pct(r["sales"], total),
    } for r in rows]


PERSON_COLUMNS = [
    col("person", "Person", link="person", params=PERSON_LINK), col("bills", "Bills", "number"),
    col("units", "Units", "number"), col("sales", "Sales", "money"), col("share", "Share", "percent"),
]


async def stock_pass() -> dict:
    """One walk over the stock ledger for every stock figure: what is on hand, what is low, what is idle."""
    from app.services import masters_service

    today = an.shop_today()
    idle_before = an.bound(today - timedelta(days=NOT_SOLD_DAYS))
    recent = an.bound(today - timedelta(days=30))
    usual = float(await masters_service.low_stock_level())
    row = (await q(f"""
        WITH m AS (
            SELECT product_id, SUM(CAST(qty AS REAL)) AS q, MIN(at) AS first_at, MAX(CASE WHEN kind = 'sell' THEN at END) AS last_sell
            FROM stock_movements GROUP BY product_id
        )
        SELECT COALESCE(SUM(CASE WHEN m.q > 0 THEN 1 ELSE 0 END), 0) AS in_stock,
               COALESCE(SUM(CASE WHEN m.q > 0 AND m.q < {LOW_BELOW} THEN 1 ELSE 0 END), 0) AS low,
               COALESCE(SUM(CASE WHEN m.q <= 0 AND m.last_sell >= ? THEN 1 ELSE 0 END), 0) AS out_recent,
               COALESCE(SUM(CASE WHEN m.q > 0 AND COALESCE(m.last_sell, m.first_at) < ? THEN 1 ELSE 0 END), 0) AS idle,
               COALESCE(SUM(CASE WHEN m.q > 0 AND COALESCE(m.last_sell, m.first_at) < ? THEN m.q * COALESCE(CAST(p.avg_cost AS REAL), 0) ELSE 0 END), 0) AS idle_value
        FROM m JOIN products p ON p.id = m.product_id
    """, [usual, recent, idle_before, idle_before]))[0]
    expiry_by = an.bound(today + timedelta(days=NEAR_EXPIRY_DAYS + 1))
    now = an.bound(today)
    exp = (await q("""
        SELECT COUNT(*) AS batches, COUNT(DISTINCT b.product_id) AS items,
               COALESCE(SUM(CASE WHEN b.expiry < ? THEN 1 ELSE 0 END), 0) AS expired
        FROM batches b
        WHERE b.expiry IS NOT NULL AND b.expiry < ?
          AND b.product_id IN (SELECT product_id FROM stock_movements GROUP BY product_id HAVING SUM(CAST(qty AS REAL)) > 0)
    """, [now, expiry_by]))[0]
    from app.services.inventory_service import stock_value

    value = await stock_value()
    return {**{k: f(v) for k, v in row.items()}, "batches": f(exp["batches"]), "expiryItems": f(exp["items"]),
            "expired": f(exp["expired"]), "valueAtCost": f(value["valueAtCost"]), "valueAtSale": f(value["valueAtSale"])}


# ── the board ───────────────────────────────────────────────────────────────────────────────────

async def board(period: Period) -> dict:
    prev = period.previous()
    cur_t, prev_t = await totals(period, Scope()), await totals(prev, Scope())
    cur_b, prev_b = await bill_totals(period), await bill_totals(prev)

    sales, psales = cur_t["sales"], prev_t["sales"]
    profit, pprofit = sales - cur_t["cost"], psales - prev_t["cost"]
    bills, pbills = cur_b["bills"], prev_b["bills"]
    avg, pavg = (cur_b["paid"] / bills if bills else 0), (prev_b["paid"] / pbills if pbills else 0)
    per_bill, pper_bill = (cur_t["units"] / bills if bills else 0), (prev_t["units"] / pbills if pbills else 0)
    returns_count = (await q(f"""
        SELECT (SELECT COUNT(*) FROM return_records r WHERE r.at >= ? AND r.at < ?)
             + (SELECT COUNT(DISTINCT sl.sale_id) FROM sale_lines sl JOIN sale_records s ON s.id = sl.sale_id
                WHERE sl.is_return = 1 AND s.at >= ? AND s.at < ?) AS n
    """, [period.start_at, period.end_at, period.start_at, period.end_at]))[0]["n"]

    tenders = await tender_rows(period)
    paid_total = sum(f(t["amount"]) for t in tenders)
    top_tender = tenders[0] if tenders else None
    hours = await q(f"""
        SELECT {SQL_HOUR} AS hour, COUNT(*) AS bills, SUM(CAST(net_value AS REAL)) AS paid
        FROM sale_records WHERE at >= ? AND at < ? GROUP BY hour ORDER BY hour
    """, [period.start_at, period.end_at])
    busiest = max(hours, key=lambda h: h["bills"]) if hours else None
    tills = (await q("""
        SELECT COUNT(*) AS n, COALESCE(SUM(CAST(variance AS REAL)), 0) AS variance,
               COALESCE(SUM(CASE WHEN CAST(variance AS REAL) < 0 THEN 1 ELSE 0 END), 0) AS short
        FROM till_sessions WHERE closed_at >= ? AND closed_at < ?
    """, [period.start_at, period.end_at]))[0]
    open_tills = (await q("SELECT COUNT(*) AS n FROM till_sessions WHERE closed_at IS NULL"))[0]["n"]

    groups = {kind: await by_group(period, Scope(), kind) for kind in ("department", "category", "brand")}
    items = await by_item(period, Scope(), limit=10)
    bottom = await by_item(period, Scope(), order="sales ASC, units ASC", limit=1, having="HAVING SUM(l.qty) > 0 AND SUM(l.sales) > 0")
    ctx = Ctx(period=period, root="staff")
    people = await person_rows(period, Scope(), ctx)
    stock = await stock_pass()

    def top_of(rows: list[dict], kind: str, kpi_id: str) -> dict:
        if not rows or f(rows[0]["sales"]) <= 0:
            return kpi(kpi_id, None, "-", "No sales in this period")
        return kpi(kpi_id, money(rows[0]["sales"]), group_label(kind, rows[0]["grp"]),
                   f"{pct(rows[0]['sales'], sales) or 0:.0f}% of sales, {rs(rows[0]['sales'])}")

    items_out = [
        kpi("sales", money(sales), rs(sales), f"GST on top {rs(cur_b['gst'])}", delta(sales, psales)),
        kpi("gross-profit", money(profit), rs(profit), f"{pct(profit, sales) or 0:.1f}% margin" if sales else "No sales in this period", delta(profit, pprofit),
            severity="bad" if profit < 0 else None),
        kpi("bills", int(bills), count(bills), f"{plural(cur_b['people'], 'person', 'people')} rang them", delta(bills, pbills)),
        kpi("average-bill", money(avg), rs(avg), f"{rs(cur_b['paid'])} paid in all" if bills else "No bills in this period", delta(avg, pavg)),
        kpi("items-per-bill", round(per_bill, 2), f"{per_bill:.1f}" if bills else "-", f"{unit_count(cur_t['units'])} in all" if bills else None, delta(per_bill, pper_bill)),
        kpi("returns", money(cur_t["returns"]), rs(cur_t["returns"]),
            f"{plural(returns_count, 'return')}, {pct(cur_t['returns'], sales + cur_t['returns']) or 0:.1f}% of what sold" if returns_count else "Nothing came back",
            delta(cur_t["returns"], prev_t["returns"], good_when_up=False)),
        kpi("discounts", money(cur_b["discount"]), rs(cur_b["discount"]),
            f"{pct(cur_b['discount'], cur_b['gross']) or 0:.1f}% off, {plural(cur_b['approvals'], 'approval')} above a limit",
            delta(cur_b["discount"], prev_b["discount"], good_when_up=None), severity="watch" if cur_b["approvals"] else None),
        kpi("payment-mix", pct(top_tender["amount"], paid_total) if top_tender else None,
            f"{top_tender['name']} {pct(top_tender['amount'], paid_total) or 0:.0f}%" if top_tender else "-",
            ", ".join(f"{t['name']} {pct(t['amount'], paid_total) or 0:.0f}%" for t in tenders[1:3]) or ("No payments in this period" if not tenders else None)),
        kpi("busy-hours", busiest["hour"] if busiest else None, hour_label(int(busiest["hour"])) if busiest else "-",
            f"{plural(busiest['bills'], 'bill')} in that hour" if busiest else "No bills in this period"),
        kpi("tills", money(tills["variance"]), rs(tills["variance"]) if tills["n"] else "-",
            f"{plural(tills['n'], 'till')} closed, {count(tills['short'])} short, {count(open_tills)} open now",
            severity=("bad" if f(tills["variance"]) < -500 else "watch" if f(tills["variance"]) else None) if tills["n"] else None),
        top_of(groups["department"], "department", "departments"),
        top_of(groups["category"], "category", "categories"),
        top_of(groups["brand"], "brand", "brands"),
        kpi("top-items", money(items[0]["sales"]) if items else None, items[0]["name"] if items else "-",
            f"{rs(items[0]['sales'])}, {unit_count(items[0]['units'])}" if items else "No sales in this period"),
        kpi("bottom-items", money(bottom[0]["sales"]) if bottom else None, bottom[0]["name"] if bottom else "-",
            f"{rs(bottom[0]['sales'])}, {unit_count(bottom[0]['units'])}" if bottom else "No sales in this period"),
        kpi("staff", len(people), plural(len(people), "person", "people"),
            f"Most: {people[0]['person']}, {rs(people[0]['sales'])}" if people else "Nobody rang a bill"),
        kpi("stock-value", money(stock["valueAtCost"]), rs(stock["valueAtCost"]),
            f"{rs(stock['valueAtSale'])} at sale price, {plural(stock['in_stock'], 'Item')} in stock"),
        kpi("low-stock", int(stock["low"]), plural(stock["low"], "Item"),
            f"{plural(stock['out_recent'], 'Item')} out of stock that sold in the last 30 days",
            severity="watch" if stock["out_recent"] else None),
        kpi("near-expiry", int(stock["batches"]), plural(stock["batches"], "batch", "batches"),
            f"{count(stock['expired'])} already past expiry" if stock["batches"] else f"Nothing expires in the next {NEAR_EXPIRY_DAYS} days",
            severity="bad" if stock["expired"] else ("watch" if stock["batches"] else None)),
        kpi("not-sold-90", int(stock["idle"]), plural(stock["idle"], "Item"),
            f"{rs(stock['idle_value'])} at cost on the shelf" if stock["idle"] else "Everything in stock has sold recently or is new",
            severity="watch" if stock["idle"] else None),
    ]
    for k in items_out:
        if k["id"] in ("stock-value", "low-stock", "near-expiry", "not-sold-90"):
            k["unit"] = "now"

    charts = [
        {
            "id": "hours", "title": "Busy hours", "subtitle": "Bills rung in each hour of the day", "kind": "bar-v", "unit": "bills",
            "points": [{"label": str(int(h["hour"])), "value": str(h["bills"]), "display": plural(h["bills"], "bill"),
                        "sub": hour_label(int(h["hour"])), "tone": "accent" if busiest and h["hour"] == busiest["hour"] else None} for h in hours],
            "emptyText": "No bills in this period", "ctaLabel": "See every hour", "ctaKpi": "busy-hours",
        },
        {
            "id": "departments", "title": "Sales by department", "subtitle": None, "kind": "bar-h", "unit": "Rs",
            "points": [{"label": group_label("department", g["grp"]), "value": money(g["sales"]), "display": rs(g["sales"]),
                        "sub": f"{pct(g['sales'], sales) or 0:.0f}%", "link": {"kpi": "group", "focus": {"groupKind": "department", "group": g["grp"]}}}
                       for g in groups["department"][:8] if f(g["sales"]) > 0],
            "emptyText": "No sales in this period", "ctaLabel": "See every department", "ctaKpi": "departments",
        },
        {
            "id": "top-items", "title": "Top Items", "subtitle": "By sales", "kind": "bar-h", "unit": "Rs",
            "points": [{"label": i["name"], "value": money(i["sales"]), "display": rs(i["sales"]), "sub": f"{unit_count(i['units'])}",
                        "link": {"kpi": "item", "focus": {"productId": str(i["product_id"])}}} for i in items[:8] if f(i["sales"]) > 0],
            "emptyText": "No sales in this period", "ctaLabel": "See the ranking", "ctaKpi": "top-items",
        },
        {
            "id": "staff", "title": "Sales by person", "subtitle": None, "kind": "bar-h", "unit": "Rs",
            "points": [{"label": p["person"], "value": p["sales"], "display": rs(p["sales"]), "sub": plural(p["bills"], "bill"),
                        "link": {"kpi": "person", "focus": {"userId": p["userId"]}}} for p in people[:8] if f(p["sales"]) > 0],
            "emptyText": "Nobody rang a bill in this period", "ctaLabel": "See each person", "ctaKpi": "staff",
        },
    ]
    return {
        "period": period.out(), "periods": an.periods_out(), "businessDate": an.shop_today().isoformat(),
        "groups": GROUP_ORDER, "items": items_out, "charts": charts,
    }


async def tender_rows(period: Period, day: date | None = None, user_id: str | None = None) -> list[dict]:
    where, params = bill_where(period, day, user_id)
    rows = await q(f"""
        SELECT t.code AS code, COALESCE(m.name, t.code) AS name, COUNT(DISTINCT t.sale_id) AS bills,
               COALESCE(SUM(CAST(t.amount AS REAL)), 0) AS amount
        FROM sale_tenders t JOIN sale_records s ON s.id = t.sale_id
        LEFT JOIN payment_methods m ON m.code = t.code
        WHERE {where} GROUP BY t.code
    """, params)
    change = (await q(f"SELECT COALESCE(SUM(CAST(s.cash_back AS REAL)), 0) AS c FROM sale_records s WHERE {where}", params))[0]["c"]
    out = []
    for r in rows:
        amount = f(r["amount"]) - (f(change) if r["code"] == "CASH" else 0)
        out.append({"code": r["code"], "name": r["name"], "bills": int(r["bills"] or 0), "amount": amount})
    return sorted(out, key=lambda r: -r["amount"])


# ── one figure opened up ────────────────────────────────────────────────────────────────────────

async def detail(kpi_id: str, period: Period, root: str | None, day: str | None, product_id: str | None, user_id: str | None,
                 group_kind: str | None, group: str | None, invoice: str | None, path: str | None, viewer=None) -> dict:
    if kpi_id not in KPIS:
        raise AnalysisError("That figure isn't one this branch has. Go back to the dashboard and pick it from there.")
    if group_kind and group_kind not in an.GROUP_COLUMNS:
        raise AnalysisError("Group by department, category or brand.")
    ctx = Ctx(
        period=period, root=root if root in BOARD else (kpi_id if kpi_id in BOARD else "sales"),
        day=an.parse_day(day, "day") if day else None, product_id=product_id or None, user_id=user_id or None,
        group_kind=group_kind or None, group=group if group not in (None, "") else None, invoice=(invoice or "").strip().upper() or None,
        path=[s for s in (path or "").split(".") if s in STEP_KEYS],
        hide_pharmacy=pharmacy_service.hides_pharmacy(viewer),
    )
    if ctx.group is not None and not ctx.group_kind:
        ctx.group_kind = "department"
    view = VIEWS[kpi_id]
    return await view(ctx)


async def _sales(ctx: Ctx) -> dict:
    p = ctx.period
    t, pt = await totals(p, Scope()), await totals(p.previous(), Scope())
    b = await bill_totals(p)
    days = await by_day(p, Scope())
    depts = await by_group(p, Scope(), "department")
    people = await person_rows(p, Scope(), ctx)
    head = kpi("sales", money(t["sales"]), rs(t["sales"]), f"{plural(b['bills'], 'bill')}, GST on top {rs(b['gst'])}", delta(t["sales"], pt["sales"]))
    return await _detail(
        ctx, "sales", head, series=fill_days(p, days, "sales"), series_label="Sales each day",
        table_title="Day by day", columns=day_columns(), rows=[day_row(r) for r in reversed(days)],
        sections=[
            section("By department", GROUP_COLUMNS_FOR("department"), group_rows(depts, "department", t["sales"]), "No sales in this period"),
            section("By person", PERSON_COLUMNS, people, "Nobody rang a bill in this period"),
        ],
        notes=[LINE_NOTE, "GST is shown on top: it is collected for the government and isn't the branch's sales."],
    )


def GROUP_COLUMNS_FOR(kind: str) -> list[dict]:  # noqa: N802 (reads like the constants beside it)
    word = GROUP_WORD[kind].capitalize()
    return [
        col("name", word, link="group", params={"groupKind": "groupKind", "group": "group"}),
        col("items", "Items", "number"), col("units", "Units", "number"), col("sales", "Sales", "money"),
        col("profit", "Gross profit", "money"), col("margin", "Margin", "percent"), col("share", "Share", "percent"),
    ]


def group_rows(rows: list[dict], kind: str, total_sales: float) -> list[dict]:
    return [{
        "groupKind": kind, "group": r["grp"], "name": group_label(kind, r["grp"]), "items": int(r["items"] or 0),
        "units": units(r["units"]), "sales": money(r["sales"]), "profit": money(f(r["sales"]) - f(r["cost"])),
        "margin": pct(f(r["sales"]) - f(r["cost"]), r["sales"]) if f(r["sales"]) > 0 else None, "share": pct(r["sales"], total_sales),
    } for r in rows]


async def _gross_profit(ctx: Ctx) -> dict:
    p = ctx.period
    t, pt = await totals(p, Scope()), await totals(p.previous(), Scope())
    profit = t["sales"] - t["cost"]
    days = await by_day(p, Scope())
    for r in days:
        r["profit"] = f(r["sales"]) - f(r["cost"])
    depts = await by_group(p, Scope(), "department")
    worst = await by_item(p, Scope(), order="(SUM(l.sales) - SUM(l.cost)) ASC", limit=50)
    head = kpi("gross-profit", money(profit), rs(profit), f"{pct(profit, t['sales']) or 0:.1f}% margin on {rs(t['sales'])}", delta(profit, pt["sales"] - pt["cost"]))
    return await _detail(
        ctx, "gross-profit", head, series=fill_days(p, days, "profit"), series_label="Gross profit each day",
        table_title="Day by day", columns=[col("day", "Day", "date", link="day", params=DAY_LINK), col("sales", "Sales", "money"),
                                            col("cost", "Cost", "money"), col("profit", "Gross profit", "money"), col("margin", "Margin", "percent")],
        rows=[{**day_row(r), "cost": money(r["cost"])} for r in reversed(days)],
        sections=[
            section("Margin by department", GROUP_COLUMNS_FOR("department"), group_rows(depts, "department", t["sales"]), "No sales in this period"),
            section("Items earning least", ITEM_COLUMNS_PROFIT, [item_row(r) for r in worst], "No sales in this period", subtitle="Lowest gross profit first"),
        ],
        notes=[LINE_NOTE, COST_NOTE],
    )


ITEM_COLUMNS = [
    col("item", "Item", link="item", params=ITEM_LINK), col("sku", "Code", "mono"), col("units", "Units", "number"),
    col("sales", "Sales", "money"), col("profit", "Gross profit", "money"), col("bills", "Bills", "number"),
]
ITEM_COLUMNS_PROFIT = [
    col("item", "Item", link="item", params=ITEM_LINK), col("sku", "Code", "mono"), col("units", "Units", "number"),
    col("sales", "Sales", "money"), col("profit", "Gross profit", "money"), col("margin", "Margin", "percent"),
]


async def _bills(ctx: Ctx) -> dict:
    p = ctx.period
    b, pb = await bill_totals(p), await bill_totals(p.previous())
    days = await by_day(p, Scope())
    latest = await bills_in(p, Scope(), limit=100)
    head = kpi("bills", int(b["bills"]), count(b["bills"]), f"{rs(b['paid'])} paid, {plural(b['people'], 'person', 'people')} rang them", delta(b["bills"], pb["bills"]))
    return await _detail(
        ctx, "bills", head, series=fill_days(p, days, "bills"), series_label="Bills each day", table_title="Day by day",
        columns=day_columns(with_profit=False), rows=[day_row(r) for r in reversed(days)],
        sections=[section("Latest bills", BILL_COLUMNS, latest, "No bills in this period", subtitle="The 100 most recent")],
        notes=["A bill counts on the day it was rung. Bills that only handed goods back aren't counted.", LINE_NOTE],
    )


async def _bill_days(p: Period, user_id: str | None = None) -> list[dict]:
    where, params = bill_where(p, None, user_id)
    return await q(f"""
        SELECT date(s.at, '+5 hours') AS day, COUNT(*) AS bills, SUM(CAST(s.net_value AS REAL)) AS paid,
               SUM(CAST(s.gross AS REAL)) AS gross, SUM(CAST(s.disc_total AS REAL)) AS discount,
               SUM(CASE WHEN s.discount_override_by_id IS NOT NULL THEN 1 ELSE 0 END) AS approvals
        FROM sale_records s WHERE {where} GROUP BY day ORDER BY day
    """, params)


async def _average_bill(ctx: Ctx) -> dict:
    p = ctx.period
    b, pb = await bill_totals(p), await bill_totals(p.previous())
    avg = b["paid"] / b["bills"] if b["bills"] else 0
    pavg = pb["paid"] / pb["bills"] if pb["bills"] else 0
    days = await _bill_days(p)
    for r in days:
        r["average"] = f(r["paid"]) / r["bills"] if r["bills"] else 0
    biggest = await bills_in(p, Scope(), limit=25, order="total DESC")
    head = kpi("average-bill", money(avg), rs(avg), f"{plural(b['bills'], 'bill')}, {rs(b['paid'])} paid", delta(avg, pavg))
    return await _detail(
        ctx, "average-bill", head, series=fill_days(p, days, "average"), series_label="Average bill each day", table_title="Day by day",
        columns=[col("day", "Day", "date", link="day", params=DAY_LINK), col("bills", "Bills", "number"), col("paid", "Paid", "money"), col("average", "Average bill", "money")],
        rows=[{"day": r["day"], "bills": r["bills"], "paid": money(r["paid"]), "average": money(r["average"])} for r in reversed(days)],
        sections=[section("Biggest bills", BILL_COLUMNS, biggest, "No bills in this period")],
        notes=["The average bill is what customers paid, GST included, divided by the number of bills."],
    )


async def _items_per_bill(ctx: Ctx) -> dict:
    p = ctx.period
    t, pt = await totals(p, Scope()), await totals(p.previous(), Scope())
    b, pb = await bill_totals(p), await bill_totals(p.previous())
    per = t["units"] / b["bills"] if b["bills"] else 0
    pper = pt["units"] / pb["bills"] if pb["bills"] else 0
    days = await by_day(p, Scope())
    for r in days:
        r["per"] = f(r["units"]) / r["bills"] if r["bills"] else 0
    fullest = await bills_in(p, Scope(), limit=25, order="units DESC")
    head = kpi("items-per-bill", round(per, 2), f"{per:.1f}" if b["bills"] else "-", f"{unit_count(t['units'])} on {plural(b['bills'], 'bill')}", delta(per, pper))
    return await _detail(
        ctx, "items-per-bill", head, series=[{"day": s["day"], "value": s["value"]} for s in fill_days(p, days, "per")],
        series_label="Units per bill each day", table_title="Day by day",
        columns=[col("day", "Day", "date", link="day", params=DAY_LINK), col("bills", "Bills", "number"), col("units", "Units", "number"), col("per", "Units per bill", "number")],
        rows=[{"day": r["day"], "bills": r["bills"], "units": units(r["units"]), "per": round(r["per"], 2)} for r in reversed(days)],
        sections=[section("Fullest bills", BILL_COLUMNS, fullest, "No bills in this period")],
        notes=["Units are counted as they were rung: a pack of six is one unit if it was rung as one, and weighed goods count by their weight."],
    )


async def _returns(ctx: Ctx) -> dict:
    p = ctx.period
    t, pt = await totals(p, Scope()), await totals(p.previous(), Scope())
    sql, params = an.lines_sql(p)
    users = await ctx.users()
    rows = await q(f"""
        WITH l AS ({sql})
        SELECT l.at AS at, l.invoice AS invoice, l.user_id AS user_id, l.source AS source, l.product_id AS product_id,
               p.name AS item, -l.qty AS qty, -l.sales AS value
        FROM l JOIN products p ON p.id = l.product_id
        WHERE l.returned = 1 ORDER BY l.at DESC LIMIT ?
    """, params + [TABLE_LIMIT])
    most = await by_item(p, Scope(), order="SUM(CASE WHEN l.returned = 1 THEN -l.sales ELSE 0 END) DESC", limit=25,
                         having="HAVING SUM(CASE WHEN l.returned = 1 THEN 1 ELSE 0 END) > 0")
    returned_by_item = {}
    if most:
        more = await q(f"""
            WITH l AS ({sql})
            SELECT product_id, SUM(-qty) AS qty, SUM(-sales) AS value FROM l WHERE returned = 1 GROUP BY product_id
        """, params)
        returned_by_item = {str(r["product_id"]): r for r in more}
    days = await by_day(p, Scope())
    head = kpi("returns", money(t["returns"]), rs(t["returns"]), f"{unit_count(t['returned_units'])} came back", delta(t["returns"], pt["returns"], good_when_up=False))
    return await _detail(
        ctx, "returns", head, series=fill_days(p, days, "returns"), series_label="Returns each day", table_title="Each return",
        columns=[col("at", "When", "datetime"), col("invoice", "Against bill", "mono", link="bill", params=BILL_LINK, carry=False),
                 col("where", "Where"), col("person", "Taken by", link="person", params=PERSON_LINK, carry=False),
                 col("item", "Item", link="item", params=ITEM_LINK, carry=False), col("qty", "Units", "number"), col("value", "Value", "money")],
        rows=[{"at": iso_at(r["at"]), "invoice": r["invoice"], "where": "On a bill" if r["source"] == "bill" else "Returns screen",
               "userId": str(r["user_id"]), "person": users.get(str(r["user_id"]), "-"), "productId": str(r["product_id"]),
               "item": r["item"], "qty": units(r["qty"]), "value": money(r["value"])} for r in rows],
        empty="Nothing came back in this period.",
        sections=[section("Most returned Items", [col("item", "Item", link="item", params=ITEM_LINK), col("returned", "Units back", "number"),
                                                  col("returnedValue", "Value back", "money"), col("units", "Units sold", "number")],
                          [{**item_row(r), "returned": units(returned_by_item.get(str(r["product_id"]), {}).get("qty")),
                            "returnedValue": money(returned_by_item.get(str(r["product_id"]), {}).get("value"))} for r in most],
                          "Nothing came back in this period.")],
        notes=["Value is what the goods sold for, before GST: handed back on a bill at the bill's price after discount, refunded on the Returns screen at what was paid less its GST.",
               "A refund counts on the day it was given and against the person who gave it."],
    )


async def _discounts(ctx: Ctx) -> dict:
    p = ctx.period
    b, pb = await bill_totals(p), await bill_totals(p.previous())
    days = await _bill_days(p)
    users = await ctx.users()
    approvals = await q("""
        SELECT s.invoice_number AS invoice, s.at AS at, s.cashier_id AS user_id, s.discount_override_by_id AS approver_id,
               CAST(s.gross AS REAL) AS gross, CAST(s.disc_total AS REAL) AS discount, CAST(s.net_value AS REAL) AS total
        FROM sale_records s WHERE s.at >= ? AND s.at < ? AND s.discount_override_by_id IS NOT NULL ORDER BY s.at DESC
    """, [p.start_at, p.end_at])
    givers = await q("""
        SELECT s.cashier_id AS user_id, COUNT(*) AS bills, SUM(CASE WHEN CAST(s.disc_total AS REAL) > 0 THEN 1 ELSE 0 END) AS discounted,
               SUM(CAST(s.disc_total AS REAL)) AS discount, SUM(CAST(s.gross AS REAL)) AS gross,
               SUM(CASE WHEN s.discount_override_by_id IS NOT NULL THEN 1 ELSE 0 END) AS approvals
        FROM sale_records s WHERE s.at >= ? AND s.at < ? GROUP BY s.cashier_id ORDER BY discount DESC
    """, [p.start_at, p.end_at])
    head = kpi("discounts", money(b["discount"]), rs(b["discount"]),
               f"{pct(b['discount'], b['gross']) or 0:.1f}% off {rs(b['gross'])}, on {plural(b['discounted'], 'bill')}",
               delta(b["discount"], pb["discount"], good_when_up=None))
    return await _detail(
        ctx, "discounts", head, series=fill_days(p, days, "discount"), series_label="Discounts each day", table_title="By person",
        columns=[col("person", "Person", link="person", params=PERSON_LINK), col("bills", "Bills", "number"), col("discounted", "With a discount", "number"),
                 col("discount", "Discount", "money"), col("percent", "Of their sales", "percent"), col("approvals", "Above their limit", "number")],
        rows=[{"userId": str(r["user_id"]), "person": users.get(str(r["user_id"]), "-"), "bills": r["bills"], "discounted": r["discounted"],
               "discount": money(r["discount"]), "percent": pct(r["discount"], r["gross"]), "approvals": r["approvals"]} for r in givers],
        sections=[
            section("Approved above the salesperson's limit",
                    [col("invoice", "Bill", "mono", link="bill", params=BILL_LINK), col("at", "When", "datetime"),
                     col("person", "Rung by", link="person", params=PERSON_LINK, carry=False), col("approver", "Approved by"),
                     col("gross", "Before discount", "money"), col("discount", "Discount", "money"), col("percent", "Discount", "percent")],
                    [{"invoice": r["invoice"], "at": iso_at(r["at"]), "userId": str(r["user_id"]), "person": users.get(str(r["user_id"]), "-"),
                      "approver": users.get(str(r["approver_id"]), "-"), "gross": money(r["gross"]), "discount": money(r["discount"]),
                      "percent": pct(r["discount"], r["gross"])} for r in approvals],
                    "No discount needed approval in this period."),
            section("Day by day", [col("day", "Day", "date", link="day", params=DAY_LINK), col("bills", "Bills", "number"),
                                   col("discount", "Discount", "money"), col("percent", "Of sales", "percent"), col("approvals", "Approvals", "number")],
                    [{"day": r["day"], "bills": r["bills"], "discount": money(r["discount"]), "percent": pct(r["discount"], r["gross"]),
                      "approvals": r["approvals"]} for r in reversed(days)], "No bills in this period"),
        ],
        notes=["Discounts are everything taken off bills: an Item's own discount and the bill discount. A bill needs approval when the bill discount is above the salesperson's own limit, and the approver is recorded on it."],
    )


async def _payment_mix(ctx: Ctx) -> dict:
    p = ctx.period
    tenders = await tender_rows(p)
    total = sum(t["amount"] for t in tenders)
    days = await q("""
        SELECT date(s.at, '+5 hours') AS day, t.code AS code, SUM(CAST(t.amount AS REAL)) AS amount
        FROM sale_tenders t JOIN sale_records s ON s.id = t.sale_id WHERE s.at >= ? AND s.at < ? GROUP BY day, t.code
    """, [p.start_at, p.end_at])
    change = {r["day"]: f(r["c"]) for r in await q("""
        SELECT date(at, '+5 hours') AS day, SUM(CAST(cash_back AS REAL)) AS c FROM sale_records WHERE at >= ? AND at < ? GROUP BY day
    """, [p.start_at, p.end_at])}
    per_day: dict[str, dict] = {}
    for r in days:
        d = per_day.setdefault(r["day"], {"day": r["day"], "cash": 0.0, "card": 0.0, "wallets": 0.0, "credit": 0.0, "other": 0.0})
        amount = f(r["amount"])
        bucket = {"CASH": "cash", "CARD": "card", "EASYPAISA": "wallets", "JAZZCASH": "wallets", "CREDIT": "credit"}.get(r["code"], "other")
        d[bucket] += amount
    for day, d in per_day.items():
        d["cash"] -= change.get(day, 0)
    top = tenders[0] if tenders else None
    head = kpi("payment-mix", pct(top["amount"], total) if top else None, f"{top['name']} {pct(top['amount'], total) or 0:.0f}%" if top else "-",
               f"{rs(total)} taken in all" if tenders else "No payments in this period")
    return await _detail(
        ctx, "payment-mix", head, table_title="By payment method",
        columns=[col("name", "Method"), col("bills", "Bills", "number"), col("amount", "Amount", "money"), col("share", "Share", "percent")],
        rows=[{"name": t["name"] + (" (after change)" if t["code"] == "CASH" else ""), "bills": t["bills"], "amount": money(t["amount"]), "share": pct(t["amount"], total)} for t in tenders],
        empty="No payments in this period.",
        sections=[section("Day by day", [col("day", "Day", "date", link="day", params=DAY_LINK), col("cash", "Cash", "money"), col("card", "Card", "money"),
                                         col("wallets", "Easypaisa and JazzCash", "money"), col("credit", "Credit", "money"), col("other", "Other", "money")],
                          [{k: (money(v) if k != "day" else v) for k, v in d.items()} for d in sorted(per_day.values(), key=lambda d: d["day"], reverse=True)],
                          "No payments in this period")],
        notes=["Cash is what stayed in the drawer: what customers handed over less the change they were given. Refunds are not taken off here; they are under Returns and in each till close.",
               "A bill paid two ways counts under both methods."],
    )


async def _busy_hours(ctx: Ctx) -> dict:
    p = ctx.period
    hours = await q(f"""
        SELECT {SQL_HOUR} AS hour, COUNT(*) AS bills, SUM(CAST(net_value AS REAL)) AS paid
        FROM sale_records WHERE at >= ? AND at < ? GROUP BY hour ORDER BY hour
    """, [p.start_at, p.end_at])
    weekdays = await q("""
        SELECT CAST(strftime('%w', at, '+5 hours') AS INTEGER) AS wd, COUNT(*) AS bills, SUM(CAST(net_value AS REAL)) AS paid,
               COUNT(DISTINCT date(at, '+5 hours')) AS days
        FROM sale_records WHERE at >= ? AND at < ? GROUP BY wd ORDER BY wd
    """, [p.start_at, p.end_at])
    busiest = max(hours, key=lambda h: h["bills"]) if hours else None
    total = sum(h["bills"] for h in hours)
    names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    head = kpi("busy-hours", busiest["hour"] if busiest else None, hour_label(int(busiest["hour"])) if busiest else "-",
               f"{plural(busiest['bills'], 'bill')} of {count(total)} in that hour" if busiest else "No bills in this period")
    return await _detail(
        ctx, "busy-hours", head, series=[{"day": str(h["hour"]), "value": str(h["bills"]), "label": hour_label(int(h["hour"]))} for h in hours],
        series_label="Bills in each hour", series_kind="bars", table_title="Hour by hour",
        columns=[col("hour", "Hour"), col("bills", "Bills", "number"), col("share", "Of all bills", "percent"), col("paid", "Paid", "money"), col("average", "Average bill", "money")],
        rows=[{"hour": hour_label(int(h["hour"])), "bills": h["bills"], "share": pct(h["bills"], total), "paid": money(h["paid"]),
               "average": money(f(h["paid"]) / h["bills"]) if h["bills"] else None} for h in hours],
        empty="No bills in this period.",
        sections=[section("Day of the week", [col("weekday", "Day"), col("days", "Days traded", "number"), col("bills", "Bills", "number"),
                                              col("perDay", "Bills a day", "number"), col("paid", "Paid", "money")],
                          [{"weekday": names[int(r["wd"])], "days": r["days"], "bills": r["bills"], "perDay": round(r["bills"] / r["days"], 1) if r["days"] else None,
                            "paid": money(r["paid"])} for r in weekdays], "No bills in this period")],
        notes=["Hours are the shop's own clock (Pakistan time). Paid is what customers paid, GST included."],
    )


async def _tills(ctx: Ctx) -> dict:
    p = ctx.period
    rows = await q("""
        SELECT ts.session_number AS session, ts.opened_at AS opened_at, ts.closed_at AS closed_at, CAST(ts.opening_float AS REAL) AS float_,
               CAST(ts.net_cash AS REAL) AS expected, CAST(ts.counted_cash AS REAL) AS counted, CAST(ts.variance AS REAL) AS variance,
               ts.opened_by_id AS user_id, c.name AS counter
        FROM till_sessions ts LEFT JOIN sales_counters c ON c.id = ts.counter_id
        WHERE ts.closed_at >= ? AND ts.closed_at < ? ORDER BY ts.closed_at DESC
    """, [p.start_at, p.end_at])
    users = await ctx.users()
    total = sum(f(r["variance"]) for r in rows)
    by_counter: dict[str, dict] = {}
    by_user: dict[str, dict] = {}
    for r in rows:
        c = by_counter.setdefault(r["counter"] or "No counter recorded", {"counter": r["counter"] or "No counter recorded", "closes": 0, "variance": 0.0, "short": 0})
        u = by_user.setdefault(str(r["user_id"]), {"userId": str(r["user_id"]), "person": users.get(str(r["user_id"]), "-"), "closes": 0, "variance": 0.0, "short": 0})
        for agg in (c, u):
            agg["closes"] += 1
            agg["variance"] += f(r["variance"])
            agg["short"] += 1 if f(r["variance"]) < 0 else 0
    open_now = (await q("""
        SELECT ts.session_number AS session, ts.opened_at AS opened_at, ts.opened_by_id AS user_id, c.name AS counter
        FROM till_sessions ts LEFT JOIN sales_counters c ON c.id = ts.counter_id WHERE ts.closed_at IS NULL ORDER BY ts.opened_at
    """))
    head = kpi("tills", money(total), rs(total) if rows else "-", f"{plural(len(rows), 'till')} closed, {sum(1 for r in rows if f(r['variance']) < 0)} short")
    return await _detail(
        ctx, "tills", head, table_title="Each till close",
        columns=[col("session", "Till", "mono"), col("counter", "Counter"), col("person", "Opened by", link="person", params=PERSON_LINK, carry=False),
                 col("openedAt", "Opened", "datetime"), col("closedAt", "Closed", "datetime"), col("expected", "Cash expected", "money"),
                 col("counted", "Cash counted", "money"), col("variance", "Over or short", "money")],
        rows=[{"session": r["session"], "counter": r["counter"] or "-", "userId": str(r["user_id"]), "person": users.get(str(r["user_id"]), "-"),
               "openedAt": iso_at(r["opened_at"]), "closedAt": iso_at(r["closed_at"]), "expected": money(r["expected"]),
               "counted": money(r["counted"]), "variance": money(r["variance"])} for r in rows],
        empty="No till was closed in this period.",
        sections=[
            section("By counter", [col("counter", "Counter"), col("closes", "Closes", "number"), col("short", "Short", "number"), col("variance", "Over or short", "money")],
                    [{**c, "variance": money(c["variance"])} for c in by_counter.values()], "No till was closed in this period."),
            section("By person", [col("person", "Person", link="person", params=PERSON_LINK), col("closes", "Closes", "number"), col("short", "Short", "number"),
                                  col("variance", "Over or short", "money")],
                    [{**u, "variance": money(u["variance"])} for u in by_user.values()], "No till was closed in this period."),
            section("Open now", [col("session", "Till", "mono"), col("counter", "Counter"), col("person", "Opened by", link="person", params=PERSON_LINK, carry=False),
                                 col("openedAt", "Opened", "datetime")],
                    [{"session": r["session"], "counter": r["counter"] or "-", "userId": str(r["user_id"]), "person": users.get(str(r["user_id"]), "-"),
                      "openedAt": iso_at(r["opened_at"])} for r in open_now], "No till is open."),
        ],
        notes=["A till close counts on the day it was closed. Over or short is the cash counted less the cash the till expected: a minus is money missing from the drawer."],
    )


async def _groups(ctx: Ctx, kpi_id: str) -> dict:
    p = ctx.period
    kind = GROUP_KPI[kpi_id]
    t = await totals(p, Scope())
    rows = await by_group(p, Scope(), kind)
    top = rows[0] if rows and f(rows[0]["sales"]) > 0 else None
    head = kpi(kpi_id, money(top["sales"]) if top else None, group_label(kind, top["grp"]) if top else "-",
               f"{pct(top['sales'], t['sales']) or 0:.0f}% of {rs(t['sales'])}" if top else "No sales in this period")
    return await _detail(
        ctx, kpi_id, head, table_title=f"Every {GROUP_WORD[kind]}", columns=GROUP_COLUMNS_FOR(kind), rows=group_rows(rows, kind, t["sales"]),
        empty="No sales in this period.", notes=[LINE_NOTE, COST_NOTE],
    )


async def _top_items(ctx: Ctx) -> dict:
    p = ctx.period
    t = await totals(p, Scope())
    rows = await by_item(p, Scope(), limit=100)
    head = kpi("top-items", money(rows[0]["sales"]) if rows else None, rows[0]["name"] if rows else "-",
               f"{rs(rows[0]['sales'])} of {rs(t['sales'])}" if rows else "No sales in this period")
    return await _detail(
        ctx, "top-items", head, table_title="The 100 best sellers",
        columns=[col("item", "Item", link="item", params=ITEM_LINK), col("sku", "Code", "mono"), col("department", "Department"),
                 col("units", "Units", "number"), col("sales", "Sales", "money"), col("share", "Share", "percent"),
                 col("profit", "Gross profit", "money"), col("bills", "Bills", "number")],
        rows=[item_row(r, t["sales"]) for r in rows], empty="No sales in this period.",
        notes=[LINE_NOTE, "For every Item ranked A, B or C, open ABC and XYZ under Reports."],
    )


async def _bottom_items(ctx: Ctx) -> dict:
    p = ctx.period
    rows = await by_item(p, Scope(), order="sales ASC, units ASC", limit=100, having="HAVING SUM(l.qty) > 0 AND SUM(l.sales) > 0")
    head = kpi("bottom-items", money(rows[0]["sales"]) if rows else None, rows[0]["name"] if rows else "-",
               f"{rs(rows[0]['sales'])}, {unit_count(rows[0]['units'])}" if rows else "No sales in this period")
    return await _detail(
        ctx, "bottom-items", head, table_title="The 100 Items that sold least",
        columns=[col("item", "Item", link="item", params=ITEM_LINK), col("sku", "Code", "mono"), col("department", "Department"),
                 col("units", "Units", "number"), col("sales", "Sales", "money"), col("bills", "Bills", "number"), col("lastSold", "Last sold", "datetime")],
        rows=[item_row(r) for r in rows], empty="No sales in this period.",
        notes=["Only Items that sold at least once in the period are here. Items on the shelf that didn't sell at all are under Reports, ABC and XYZ, Sold least."],
    )


async def _staff(ctx: Ctx) -> dict:
    p = ctx.period
    people = await person_rows(p, Scope(), ctx)
    discounts = {str(r["user_id"]): r for r in await q("""
        SELECT cashier_id AS user_id, SUM(CAST(disc_total AS REAL)) AS discount, SUM(CAST(net_value AS REAL)) AS paid,
               SUM(CASE WHEN discount_override_by_id IS NOT NULL THEN 1 ELSE 0 END) AS approvals
        FROM sale_records WHERE at >= ? AND at < ? GROUP BY cashier_id
    """, [p.start_at, p.end_at])}
    rows = [{**r, "discount": money(discounts.get(r["userId"], {}).get("discount")),
             "average": money(f(discounts.get(r["userId"], {}).get("paid")) / r["bills"]) if r["bills"] else None,
             "approvals": int(discounts.get(r["userId"], {}).get("approvals") or 0)} for r in people]
    head = kpi("staff", len(people), plural(len(people), "person", "people"),
               f"Most: {people[0]['person']}, {rs(people[0]['sales'])}" if people else "Nobody rang a bill")
    return await _detail(
        ctx, "staff", head, table_title="Each person",
        columns=[col("person", "Person", link="person", params=PERSON_LINK), col("bills", "Bills", "number"), col("sales", "Sales", "money"),
                 col("share", "Share", "percent"), col("average", "Average bill", "money"), col("units", "Units", "number"),
                 col("discount", "Discounts given", "money"), col("approvals", "Above their limit", "number"), col("refunds", "Refunds given", "money")],
        rows=rows, empty="Nobody rang a bill in this period.",
        notes=["A person's sales are the bills they rang, less refunds they gave on the Returns screen. Average bill is what their customers paid, GST included."],
    )


async def _stock_value(ctx: Ctx) -> dict:
    stock = await stock_pass()
    depts = await q("""
        WITH m AS (SELECT product_id, SUM(CAST(qty AS REAL)) AS q FROM stock_movements GROUP BY product_id HAVING q > 0)
        SELECT COALESCE(NULLIF(p.department, ''), ?) AS grp, COUNT(*) AS items, SUM(m.q) AS units,
               SUM(m.q * COALESCE(CAST(p.avg_cost AS REAL), 0)) AS cost, SUM(m.q * COALESCE(CAST(p.price AS REAL), 0)) AS sale
        FROM m JOIN products p ON p.id = m.product_id GROUP BY grp ORDER BY cost DESC
    """, [an.NO_GROUP])
    top = await q("""
        WITH m AS (SELECT product_id, SUM(CAST(qty AS REAL)) AS q FROM stock_movements GROUP BY product_id HAVING q > 0)
        SELECT p.id AS product_id, p.name AS name, p.sku AS sku, m.q AS on_hand, CAST(p.avg_cost AS REAL) AS avg_cost,
               m.q * COALESCE(CAST(p.avg_cost AS REAL), 0) AS cost
        FROM m JOIN products p ON p.id = m.product_id ORDER BY cost DESC LIMIT 100
    """)
    head = kpi("stock-value", money(stock["valueAtCost"]), rs(stock["valueAtCost"]), f"{rs(stock['valueAtSale'])} at sale price, {plural(stock['in_stock'], 'Item')} in stock")
    return await _detail(
        ctx, "stock-value", head, table_title="By department",
        columns=[col("name", "Department"), col("items", "Items", "number"), col("units", "Units", "number"),
                 col("cost", "At cost", "money"), col("sale", "At sale price", "money"), col("share", "Share", "percent")],
        rows=[{"name": group_label("department", r["grp"]), "items": r["items"], "units": units(r["units"]), "cost": money(r["cost"]),
               "sale": money(r["sale"]), "share": pct(r["cost"], stock["valueAtCost"])} for r in depts],
        sections=[section("Most money on the shelf", [col("item", "Item", link="item", params=ITEM_LINK), col("sku", "Code", "mono"),
                                                       col("onHand", "On hand", "number"), col("avgCost", "Average cost", "money"), col("cost", "At cost", "money")],
                          [{"productId": str(r["product_id"]), "item": r["name"], "sku": r["sku"], "onHand": units(r["on_hand"]),
                            "avgCost": money(r["avg_cost"]), "cost": money(r["cost"])} for r in top], "Nothing in stock")],
        notes=["Stock is as it stands now, whatever period is picked: every location in the branch, at each Item's average cost. The total matches Inventory Reports."],
    )


async def _low_stock(ctx: Ctx) -> dict:
    from app.services import masters_service

    stock = await stock_pass()
    since = an.bound(an.shop_today() - timedelta(days=29))
    usual = await masters_service.low_stock_level()
    rows = await q(f"""
        WITH m AS (SELECT product_id, SUM(CAST(qty AS REAL)) AS q, MAX(CASE WHEN kind = 'sell' THEN at END) AS last_sell,
                          SUM(CASE WHEN kind = 'sell' AND at >= ? THEN -CAST(qty AS REAL) ELSE 0 END) AS sold30
                   FROM stock_movements GROUP BY product_id)
        SELECT p.id AS product_id, p.name AS name, p.sku AS sku, p.department AS department, m.q AS on_hand, m.sold30 AS sold30, m.last_sell AS last_sell,
               CAST(p.reorder_level AS REAL) AS reorder_level
        FROM m JOIN products p ON p.id = m.product_id
        WHERE (m.q > 0 AND m.q < {LOW_BELOW}) OR (m.q <= 0 AND m.last_sell >= ?)
        ORDER BY CASE WHEN m.sold30 > 0 THEN m.q / (m.sold30 / 30.0) ELSE 1e18 END, m.q LIMIT ?
    """, [since, float(usual), an.bound(an.shop_today() - timedelta(days=30)), 500])
    head = kpi("low-stock", int(stock["low"]), plural(stock["low"], "Item"), f"{plural(stock['out_recent'], 'Item')} out of stock that sold in the last 30 days")
    out = []
    for r in rows:
        rate = f(r["sold30"]) / 30
        out.append({"productId": str(r["product_id"]), "item": r["name"], "sku": r["sku"], "department": r["department"],
                    "onHand": units(r["on_hand"]), "level": units(r["reorder_level"] if r["reorder_level"] is not None else usual), "sold30": units(r["sold30"]), "cover": round(max(f(r["on_hand"]), 0) / rate) if rate > 0 else None,
                    "lastSold": iso_at(r["last_sell"])})
    return await _detail(
        ctx, "low-stock", head, table_title="Running out first",
        columns=[col("item", "Item", link="item", params=ITEM_LINK), col("sku", "Code", "mono"), col("department", "Department"),
                 col("onHand", "On hand", "number"), col("level", "Low below", "number"), col("sold30", "Sold in 30 days", "number"), col("cover", "Days left", "number"), col("lastSold", "Last sold", "datetime")],
        rows=out, empty="Nothing is running low.",
        notes=[f"Low means some stock left but fewer than the Item's reorder level, set on the Item. Items without one use the branch's usual low stock level, now {units(usual)}. Items already out that sold in the last 30 days are listed too.",
               "Days left is today's stock divided by what the Item sold a day over the last 30 days; the list starts with what runs out soonest. Up to 500 Items are shown."],
    )


async def _near_expiry(ctx: Ctx) -> dict:
    today = an.shop_today()
    rows = await q("""
        WITH m AS (SELECT product_id, SUM(CAST(qty AS REAL)) AS q FROM stock_movements GROUP BY product_id HAVING q > 0)
        SELECT b.product_id AS product_id, p.name AS name, p.sku AS sku, b.lot_number AS lot, b.expiry AS expiry,
               CAST(b.received_qty AS REAL) AS received, m.q AS on_hand
        FROM batches b JOIN m ON m.product_id = b.product_id JOIN products p ON p.id = b.product_id
        WHERE b.expiry IS NOT NULL AND b.expiry < ? ORDER BY b.expiry LIMIT 500
    """, [an.bound(today + timedelta(days=NEAR_EXPIRY_DAYS + 1))])
    out = []
    for r in rows:
        expires = datetime_day(r["expiry"])
        out.append({"productId": str(r["product_id"]), "item": r["name"], "sku": r["sku"], "lot": r["lot"] or "-",
                    "expiry": expires.isoformat() if expires else None, "daysLeft": (expires - today).days if expires else None,
                    "received": units(r["received"]), "onHand": units(r["on_hand"])})
    expired = sum(1 for r in out if r["daysLeft"] is not None and r["daysLeft"] < 0)
    head = kpi("near-expiry", len(out), plural(len(out), "batch", "batches"), f"{count(expired)} already past expiry" if out else f"Nothing expires in the next {NEAR_EXPIRY_DAYS} days")
    return await _detail(
        ctx, "near-expiry", head, table_title="Soonest first",
        columns=[col("item", "Item", link="item", params=ITEM_LINK), col("sku", "Code", "mono"), col("lot", "Batch"), col("expiry", "Expires", "date"),
                 col("daysLeft", "Days left", "number"), col("received", "Received", "number"), col("onHand", "Item on hand", "number")],
        rows=out, empty=f"Nothing in stock expires in the next {NEAR_EXPIRY_DAYS} days.",
        notes=["Batches are listed for Items that still have stock. On hand is the whole Item, not only that batch: the branch doesn't count stock batch by batch.",
               "A minus in Days left means the batch is already past its expiry date."],
    )


def datetime_day(value) -> date | None:
    if not value:
        return None
    text = str(value).replace("T", " ")
    try:
        # A stored time is a UTC instant (with or without its zone written); its day is the Pakistan one.
        return pk_day(datetime.fromisoformat(text))
    except ValueError:
        return as_day(text)


async def _not_sold_90(ctx: Ctx) -> dict:
    stock = await stock_pass()
    cutoff = an.bound(an.shop_today() - timedelta(days=NOT_SOLD_DAYS))
    rows = await q("""
        WITH m AS (SELECT product_id, SUM(CAST(qty AS REAL)) AS q, MIN(at) AS first_at, MAX(CASE WHEN kind = 'sell' THEN at END) AS last_sell
                   FROM stock_movements GROUP BY product_id)
        SELECT p.id AS product_id, p.name AS name, p.sku AS sku, p.department AS department, m.q AS on_hand, m.first_at AS first_at,
               m.last_sell AS last_sell, m.q * COALESCE(CAST(p.avg_cost AS REAL), 0) AS cost
        FROM m JOIN products p ON p.id = m.product_id
        WHERE m.q > 0 AND COALESCE(m.last_sell, m.first_at) < ?
        ORDER BY cost DESC LIMIT 500
    """, [cutoff])
    head = kpi("not-sold-90", int(stock["idle"]), plural(stock["idle"], "Item"),
               f"{rs(stock['idle_value'])} at cost on the shelf" if stock["idle"] else "Everything in stock has sold recently or is new")
    return await _detail(
        ctx, "not-sold-90", head, table_title="Most money first",
        columns=[col("item", "Item", link="item", params=ITEM_LINK), col("sku", "Code", "mono"), col("department", "Department"),
                 col("onHand", "On hand", "number"), col("cost", "At cost", "money"), col("lastSold", "Last sold", "datetime"), col("firstIn", "First came in", "datetime")],
        rows=[{"productId": str(r["product_id"]), "item": r["name"], "sku": r["sku"], "department": r["department"], "onHand": units(r["on_hand"]),
               "cost": money(r["cost"]), "lastSold": iso_at(r["last_sell"]), "firstIn": iso_at(r["first_at"])} for r in rows],
        empty=f"Every Item in stock has sold in the last {NOT_SOLD_DAYS} days, or came in less than {NOT_SOLD_DAYS} days ago.",
        sections=[],
        notes=[f"Counted now, whatever period is picked. An Item that never sold counts from when it first came in, so stock received in the last {NOT_SOLD_DAYS} days isn't listed. Up to 500 Items are shown, most money first.",
               "For any period, Reports, ABC and XYZ, Sold least lists every Item that sold least or not at all."],
    )


# ── the views a figure opens into ───────────────────────────────────────────────────────────────

async def _day(ctx: Ctx) -> dict:
    if not ctx.day:
        raise AnalysisError("Pick a day first.")
    p, scope = ctx.period, ctx.scope()
    t = await totals(p, scope)
    b = await bill_totals(p, ctx.day, ctx.user_id)
    hours = await q(f"""
        SELECT {SQL_HOUR} AS hour, COUNT(*) AS bills FROM sale_records WHERE at >= ? AND at < ? GROUP BY hour ORDER BY hour
    """, [an.bound(ctx.day), an.bound(ctx.day + timedelta(days=1))])
    items = await by_item(p, scope)
    tenders = await tender_rows(p, ctx.day)
    head = kpi("sales", money(t["sales"]), rs(t["sales"]),
               f"{plural(b['bills'], 'bill')}, {unit_count(t['units'])}, gross profit {rs(t['sales'] - t['cost'])}")
    return await _detail(
        ctx, "day", head, label=day_label(ctx.day),
        series=[{"day": str(h["hour"]), "value": str(h["bills"]), "label": hour_label(int(h["hour"]))} for h in hours], series_label="Bills in each hour", series_kind="bars",
        table_title="Items sold", columns=ITEM_COLUMNS, rows=[item_row(r, t["sales"]) for r in items], empty="Nothing sold on this day.",
        sections=[
            section("Who sold", PERSON_COLUMNS, await person_rows(p, scope, ctx), "Nobody rang a bill on this day."),
            section("Bills", BILL_COLUMNS, await bills_in(p, scope), "No bills on this day."),
            section("Payments", [col("name", "Method"), col("bills", "Bills", "number"), col("amount", "Amount", "money")],
                    [{"name": x["name"], "bills": x["bills"], "amount": money(x["amount"])} for x in tenders], "No payments on this day."),
        ],
        notes=[LINE_NOTE],
    )


async def _group(ctx: Ctx) -> dict:
    if ctx.group is None:
        raise AnalysisError("Pick a department, category or brand first.")
    p, scope = ctx.period, ctx.scope()
    t = await totals(p, scope)
    everything = await totals(p, Scope(day=ctx.day))
    items = await by_item(p, scope)
    name = group_label(ctx.group_kind, ctx.group)
    head = kpi("sales", money(t["sales"]), rs(t["sales"]),
               f"{pct(t['sales'], everything['sales']) or 0:.1f}% of all sales, {plural(t['items'], 'Item')}, gross profit {rs(t['sales'] - t['cost'])}")
    sections = [section("Who sells it", PERSON_COLUMNS, await person_rows(p, scope, ctx), "Nobody sold from it in this period.")]
    series = None
    if not ctx.day:
        days = await by_day(p, scope)
        series = fill_days(p, days, "sales")
        sections.insert(0, section("Day by day", day_columns(link_kpi="group"), [day_row(r) for r in reversed(days)], "Nothing sold in this period."))
    return await _detail(
        ctx, "group", head, label=f"{GROUP_WORD[ctx.group_kind].capitalize()}: {name}", series=series, series_label="Sales each day",
        table_title="Items", columns=ITEM_COLUMNS, rows=[item_row(r, t["sales"]) for r in items], empty="Nothing sold from it in this period.",
        sections=sections, notes=[LINE_NOTE, COST_NOTE],
    )


async def _item(ctx: Ctx) -> dict:
    if not ctx.product_id:
        raise AnalysisError("Pick an Item first.")
    return await item_view(ctx, "item")


async def item_view(ctx: Ctx, kpi_id: str, extra_notes: list[str] | None = None, series: list[dict] | None = None,
                    series_label: str | None = None, series_kind: str = "line", lead: list[dict] | None = None) -> dict:
    p, scope = ctx.period, ctx.scope(department=True, category=True, brand=True)
    name = await ctx.item_name(ctx.product_id)
    info = (await q("""
        SELECT p.sku, p.department, p.category, p.brand, CAST(p.price AS REAL) AS price, CAST(p.avg_cost AS REAL) AS avg_cost,
               (SELECT SUM(CAST(qty AS REAL)) FROM stock_movements m WHERE m.product_id = p.id) AS on_hand,
               (SELECT MAX(at) FROM stock_movements m WHERE m.product_id = p.id AND m.kind = 'sell') AS last_sell,
               (SELECT MIN(at) FROM stock_movements m WHERE m.product_id = p.id) AS first_at
        FROM products p WHERE p.id = ?
    """, [ctx.product_id]))[0]
    t = await totals(p, scope)
    users = await ctx.users()
    people = await person_rows(p, scope, ctx)
    bills = await bills_in(p, scope, limit=200)
    sections: list[dict] = list(lead or [])
    if not ctx.day and series is None:
        days = await by_day(p, scope)
        series, series_label = fill_days(p, days, "units"), "Units sold each day"
        for r in days:
            r["units_text"] = units(r["units"])
        sections.append(section("Day by day", [col("day", "Day", "date", link="item", params=DAY_LINK), col("bills", "Bills", "number"),
                                               col("units", "Units", "number"), col("sales", "Sales", "money"), col("returns", "Returns", "money")],
                                [day_row(r) for r in reversed(days)], "It didn't sell in this period."))
    sections.append(section("Bills", BILL_COLUMNS, bills, "It wasn't on any bill in this period.", subtitle="The 200 most recent" if len(bills) >= 200 else None))
    sections.append(section("The Item", [col("label", "What"), col("value", "Now")], [
        {"label": "Code", "value": info["sku"]},
        {"label": "Department, category, brand", "value": ", ".join(x for x in (info["department"], info["category"], info["brand"]) if x) or "-"},
        {"label": "Sale price", "value": rs(info["price"])},
        {"label": "Average cost", "value": rs(info["avg_cost"])},
        {"label": "On hand", "value": qty_text(info["on_hand"])},
        {"label": "Last sold", "value": _when(info["last_sell"])},
        {"label": "First came in", "value": _when(info["first_at"])},
    ], "-", facts=True))
    per = f"{unit_count(t['units'])} on {plural(t['bills'], 'bill')}, gross profit {rs(t['sales'] - t['cost'])}"
    if t["returned_units"]:
        per += f", {qty_text(t['returned_units'])} came back"
    head = kpi("sales", money(t["sales"]), rs(t["sales"]), per)
    return await _detail(
        ctx, kpi_id, head, label=name, series=series, series_label=series_label, series_kind=series_kind,
        table_title="Who sold it", columns=[col("person", "Person", link="item-person", params=PERSON_LINK), col("bills", "Bills", "number"),
                                            col("units", "Units", "number"), col("sales", "Sales", "money"), col("share", "Share", "percent")],
        rows=people, empty="Nobody sold it in this period.", sections=sections,
        notes=(extra_notes or []) + [LINE_NOTE, COST_NOTE],
    )


def _when(value) -> str:
    day = datetime_day(value)
    return day_label(day) if day else "-"


def _when_time(value) -> str:
    try:
        at = pk_time(datetime.fromisoformat(str(value).replace("T", " ")))
    except ValueError:
        return str(value)
    return f"{day_label(at.date())}, {at.hour % 12 or 12}:{at.minute:02d} {'am' if at.hour < 12 else 'pm'}"


async def _person(ctx: Ctx) -> dict:
    if not ctx.user_id:
        raise AnalysisError("Pick a person first.")
    p, scope = ctx.period, ctx.scope()
    users = await ctx.users()
    name = users.get(ctx.user_id)
    if name is None:
        raise AnalysisError("That person isn't on this branch's staff list.")
    t = await totals(p, scope)
    b = await bill_totals(p, ctx.day, ctx.user_id)
    items = await by_item(p, scope)
    sections = []
    series = None
    if not ctx.day:
        days = await by_day(p, scope)
        series = fill_days(p, days, "sales")
        sections.append(section("Day by day", day_columns(link_kpi="person"), [day_row(r) for r in reversed(days)], "They sold nothing in this period."))
    sections.append(section("Bills", BILL_COLUMNS, await bills_in(p, scope, limit=200), "They rang no bills in this period."))
    where, params = bill_where(p, ctx.day, ctx.user_id)
    approvals = await q(f"""
        SELECT s.invoice_number AS invoice, s.at AS at, s.discount_override_by_id AS approver_id,
               CAST(s.gross AS REAL) AS gross, CAST(s.disc_total AS REAL) AS discount
        FROM sale_records s WHERE {where} AND s.discount_override_by_id IS NOT NULL ORDER BY s.at DESC
    """, params)
    sections.append(section("Discounts above their limit", [col("invoice", "Bill", "mono", link="bill", params=BILL_LINK), col("at", "When", "datetime"),
                                                             col("approver", "Approved by"), col("discount", "Discount", "money"), col("percent", "Discount", "percent")],
                            [{"invoice": r["invoice"], "at": iso_at(r["at"]), "approver": users.get(str(r["approver_id"]), "-"),
                              "discount": money(r["discount"]), "percent": pct(r["discount"], r["gross"])} for r in approvals],
                            "None needed approval."))
    start, end = (an.bound(ctx.day), an.bound(ctx.day + timedelta(days=1))) if ctx.day else (p.start_at, p.end_at)
    refunds = await q("""
        SELECT r.at AS at, s.invoice_number AS invoice, CAST(r.refund_total AS REAL) AS total, r.refund_method AS method
        FROM return_records r LEFT JOIN sale_records s ON s.id = r.against_id
        WHERE r.cashier_id = ? AND r.at >= ? AND r.at < ? ORDER BY r.at DESC
    """, [ctx.user_id, start, end])
    sections.append(section("Refunds they gave", [col("at", "When", "datetime"), col("invoice", "Against bill", "mono", link="bill", params=BILL_LINK),
                                                   col("method", "Paid back as"), col("total", "Refund", "money")],
                            [{"at": iso_at(r["at"]), "invoice": r["invoice"], "method": (r["method"] or "").capitalize(), "total": money(r["total"])} for r in refunds],
                            "They gave no refunds."))
    tills = await q("""
        SELECT ts.session_number AS session, c.name AS counter, ts.opened_at AS opened_at, ts.closed_at AS closed_at,
               CAST(ts.net_cash AS REAL) AS expected, CAST(ts.counted_cash AS REAL) AS counted, CAST(ts.variance AS REAL) AS variance
        FROM till_sessions ts LEFT JOIN sales_counters c ON c.id = ts.counter_id
        WHERE ts.opened_by_id = ? AND ts.closed_at >= ? AND ts.closed_at < ? ORDER BY ts.closed_at DESC
    """, [ctx.user_id, start, end])
    sections.append(section("Till closes", [col("session", "Till", "mono"), col("counter", "Counter"), col("openedAt", "Opened", "datetime"),
                                             col("closedAt", "Closed", "datetime"), col("expected", "Cash expected", "money"),
                                             col("counted", "Cash counted", "money"), col("variance", "Over or short", "money")],
                            [{"session": r["session"], "counter": r["counter"] or "-", "openedAt": iso_at(r["opened_at"]), "closedAt": iso_at(r["closed_at"]),
                              "expected": money(r["expected"]), "counted": money(r["counted"]), "variance": money(r["variance"])} for r in tills],
                            "They closed no till."))
    duty = await q("""
        SELECT c.name AS counter, COUNT(*) AS spells,
               SUM((julianday(COALESCE(d.ended_at, CURRENT_TIMESTAMP)) - julianday(d.started_at)) * 24) AS hours
        FROM counter_duties d JOIN sales_counters c ON c.id = d.counter_id
        WHERE d.user_id = ? AND d.started_at >= ? AND d.started_at < ? GROUP BY c.name
    """, [ctx.user_id, start, end])
    sections.append(section("Counter duty", [col("counter", "Counter"), col("spells", "Times put on it", "number"), col("hours", "Hours", "number")],
                            [{"counter": r["counter"], "spells": r["spells"], "hours": round(f(r["hours"]), 1)} for r in duty], "No counter duty recorded."))
    head = kpi("sales", money(t["sales"]), rs(t["sales"]),
               f"{plural(b['bills'], 'bill')}, average bill {rs(b['paid'] / b['bills']) if b['bills'] else '-'}, discounts {rs(b['discount'])}")
    return await _detail(
        ctx, "person", head, label=name, series=series, series_label="Sales each day",
        table_title="What they sold", columns=[col("item", "Item", link="item-person", params=ITEM_LINK), col("sku", "Code", "mono"),
                                               col("units", "Units", "number"), col("sales", "Sales", "money"), col("bills", "Bills", "number")],
        rows=[item_row(r, t["sales"]) for r in items], empty="They sold nothing in this period.", sections=sections,
        notes=["A person's sales are the bills they rang, less refunds they gave on the Returns screen.", LINE_NOTE],
    )


async def _item_person(ctx: Ctx) -> dict:
    if not ctx.product_id or not ctx.user_id:
        raise AnalysisError("Pick an Item and a person first.")
    p, scope = ctx.period, ctx.scope()
    users = await ctx.users()
    item, person = await ctx.item_name(ctx.product_id), users.get(ctx.user_id, "-")
    t = await totals(p, scope)
    everyone = await totals(p, ctx.scope(user_id=True))
    bills = await bills_in(p, scope, limit=TABLE_LIMIT)
    sections = []
    series = None
    if not ctx.day:
        days = await by_day(p, scope)
        series = fill_days(p, days, "units")
        sections.append(section("Day by day", day_columns(link_kpi="item-person", with_profit=False), [day_row(r) for r in reversed(days)], "Nothing in this period."))
    head = kpi("sales", money(t["sales"]), rs(t["sales"]),
               f"{unit_count(t['units'])} on {plural(t['bills'], 'bill')}, {pct(t['sales'], everyone['sales']) or 0:.0f}% of this Item's sales")
    return await _detail(
        ctx, "item-person", head, label=f"{item} sold by {person}", series=series, series_label="Units each day",
        table_title="Bills", columns=BILL_COLUMNS, rows=bills, empty="They didn't sell it in this period.", sections=sections, notes=[LINE_NOTE],
    )


async def _bill(ctx: Ctx) -> dict:
    if not ctx.invoice:
        raise AnalysisError("Pick a bill first.")
    rows = await q("""
        SELECT s.id AS id, s.invoice_number AS invoice, s.at AS at, s.cashier_id AS user_id, s.discount_override_by_id AS approver_id,
               CAST(s.gross AS REAL) AS gross, CAST(s.disc_total AS REAL) AS discount, CAST(s.fare AS REAL) AS fare, CAST(s.gst AS REAL) AS gst,
               CAST(s.net_value AS REAL) AS total, CAST(s.received AS REAL) AS received, CAST(s.cash_back AS REAL) AS cash_back,
               s.fbr_invoice_number AS fbr, s.earned_points AS points, s.points_redeemed AS redeemed, s.till_session_id AS till_id,
               pa.name AS party, pa.code AS party_code, pa.is_walk_in AS walk_in, m.name AS member, s.slips AS slips
        FROM sale_records s LEFT JOIN parties pa ON pa.id = s.party_id LEFT JOIN members m ON m.id = s.member_id
        WHERE s.invoice_number = ?
    """, [ctx.invoice])
    if not rows:
        raise AnalysisError(f"There is no bill {ctx.invoice} at this branch.")
    s = rows[0]
    users = await ctx.users()
    # Bills rung before counters arrived don't name their till; the drawer that was open at the time, for that person, is it.
    till = await q("""
        SELECT ts.session_number AS session, c.name AS counter, ts.opened_by_id AS opened_by, 1 AS linked
        FROM till_sessions ts LEFT JOIN sales_counters c ON c.id = ts.counter_id WHERE ts.id = ?
    """, [s["till_id"]]) if s["till_id"] else await q("""
        SELECT ts.session_number AS session, c.name AS counter, ts.opened_by_id AS opened_by, 0 AS linked
        FROM till_sessions ts LEFT JOIN sales_counters c ON c.id = ts.counter_id
        WHERE ts.opened_at <= ? AND (ts.closed_at IS NULL OR ts.closed_at >= ?)
        ORDER BY CASE WHEN ts.opened_by_id = ? THEN 0 ELSE 1 END, ts.opened_at DESC LIMIT 1
    """, [s["at"], s["at"], s["user_id"]])
    lines = await q("""
        SELECT sl.product_id AS product_id, p.name AS name, p.sku AS sku, CAST(sl.qty AS REAL) AS qty, CAST(sl.unit_price AS REAL) AS price,
               sl.is_return AS is_return, CAST(sl.disc_amount AS REAL) AS disc, CAST(sl.tax_amount AS REAL) AS tax,
               COALESCE(CAST(sl.unit_cost AS REAL), CAST(p.avg_cost AS REAL), 0) AS unit_cost, CAST(p.tax_rate AS REAL) AS tax_rate,
               p.department AS department
        FROM sale_lines sl JOIN products p ON p.id = sl.product_id WHERE sl.sale_id = ?
    """, [s["id"]])
    bill_day = datetime_day(s["at"])
    unseen = {d.upper() for d in await pharmacy_service.departments()} if ctx.hide_pharmacy else set()
    slips = _json_list(s.get("slips"))
    out_lines = []
    folded: dict | None = None
    for l in lines:
        sign = -1 if l["is_return"] else 1
        value = sign * l["qty"] * l["price"]
        disc = l["disc"] if l["disc"] is not None else (s["discount"] * value / s["gross"] if s["gross"] else 0)
        tax = l["tax"] if l["tax"] is not None else (value - disc) * f(l["tax_rate"]) / 100
        if (l["department"] or "").strip().upper() in unseen:
            # The bill's Pharmacy Items as one line, at what they came to.
            if folded is None:
                folded = {"count": 0, "value": 0.0, "disc": 0.0, "tax": 0.0, "profit": 0.0, "at": len(out_lines)}
                out_lines.append({})
            folded["count"] += 1
            folded["value"] += value
            folded["disc"] += disc
            folded["tax"] += tax
            folded["profit"] += value - disc - sign * l["qty"] * l["unit_cost"]
            continue
        out_lines.append({
            "productId": str(l["product_id"]), "day": bill_day.isoformat() if bill_day else None, "item": l["name"] + (" (handed back)" if l["is_return"] else ""),
            "sku": l["sku"], "qty": units(sign * l["qty"]), "price": money(l["price"]), "discount": money(disc), "sales": money(value - disc),
            "gst": money(tax), "lineTotal": money(value - disc + tax), "profit": money(value - disc - sign * l["qty"] * l["unit_cost"]),
        })
    if folded is not None:
        worth = folded["value"] - folded["disc"] + folded["tax"]
        out_lines[folded["at"]] = {
            "productId": None, "day": bill_day.isoformat() if bill_day else None,
            "item": pharmacy_service.folded_name(slips, folded["count"], Decimal(str(round(worth, 2)))),
            "sku": ", ".join(pharmacy_service.slip_numbers(slips)), "qty": units(1), "price": money(folded["value"]),
            "discount": money(folded["disc"]), "sales": money(folded["value"] - folded["disc"]), "gst": money(folded["tax"]),
            "lineTotal": money(worth), "profit": money(folded["profit"]),
        }
    tenders = await q("""
        SELECT COALESCE(m.name, t.code) AS name, t.code AS code, CAST(t.amount AS REAL) AS amount, t.reference AS reference,
               t.transaction_id AS txn, t.account AS account
        FROM sale_tenders t LEFT JOIN payment_methods m ON m.code = t.code WHERE t.sale_id = ?
    """, [s["id"]])
    tender_rows_out = [{"name": t["name"], "amount": money(t["amount"]),
                        "reference": " ".join(x for x in (t["reference"], t["txn"], t["account"]) if x) or "-"} for t in tenders]
    if f(s["cash_back"]):
        tender_rows_out.append({"name": "Change given", "amount": money(-f(s["cash_back"])), "reference": "-"})
    refunds = await q("""
        SELECT r.id AS rid, r.at AS at, r.cashier_id AS user_id, r.refund_method AS method, CAST(r.refund_total AS REAL) AS total, r.note AS note,
               rl.product_id AS product_id, p.name AS item, CAST(rl.qty AS REAL) AS qty, p.department AS department,
               CAST(rl.qty AS REAL) * CAST(rl.unit_price AS REAL) AS value
        FROM return_records r LEFT JOIN return_lines rl ON rl.return_record_id = r.id LEFT JOIN products p ON p.id = rl.product_id
        WHERE r.against_id = ? ORDER BY r.at
    """, [s["id"]])
    t = till[0] if till else None
    person = users.get(str(s["user_id"]), "-")
    facts = [
        {"label": "Bill", "value": s["invoice"]},
        {"label": "When", "value": _when_time(s["at"])},
        {"label": "Rung by", "value": person, "linkKpi": "person", "userId": str(s["user_id"])},
        {"label": "Counter", "value": (t["counter"] or "-") if t else "-"},
        {"label": "Till", "value": (t["session"] + ("" if t["linked"] else " (open at the time)")) if t else "-"},
        {"label": "Customer", "value": "Walk-in customer" if s["walk_in"] else f"{s['party']} ({s['party_code']})"},
        {"label": "Member", "value": s["member"] or "-"},
        {"label": "Before discount", "value": rs(s["gross"])},
        {"label": "Discount", "value": f"{rs(s['discount'])} ({pct(s['discount'], s['gross']) or 0:.1f}%)" if s["discount"] else "None"},
        {"label": "Discount approved by", "value": users.get(str(s["approver_id"]), "-") if s["approver_id"] else "Not needed"},
        {"label": "GST", "value": rs(s["gst"])},
        {"label": "Other charges", "value": rs(s["fare"]) if s["fare"] else "None"},
        {"label": "Bill total", "value": rs(s["total"])},
        {"label": "Points", "value": f"{s['points']} earned, {s['redeemed']} spent"},
        {"label": "FBR invoice", "value": s["fbr"] or "-"},
    ]
    refunded = sum(f(r["total"]) for r in {r["rid"]: r for r in refunds}.values())
    head = kpi("sales", money(s["total"]), rs(s["total"]),
               f"{plural(len(lines), 'line')}, rung by {person}" + (f", {rs(refunded)} refunded since" if refunds else ""))
    return await _detail(
        ctx, "bill", head, label=f"Bill {s['invoice']}",
        table_title="Lines", columns=[
            col("item", "Item", link="item", params=ITEM_LINK, carry=False), col("sku", "Code", "mono"),
            col("qty", "Units", "number"), col("price", "Price", "money"), col("discount", "Discount", "money"), col("sales", "Sales", "money"),
            col("gst", "GST", "money"), col("lineTotal", "Line total", "money"), col("profit", "Gross profit", "money")],
        rows=out_lines, empty="This bill has no lines.",
        sections=[
            section("About this bill", [col("label", "What"), col("value", "Detail", link_key="linkKpi", params=PERSON_LINK, carry=False)], facts, "-", facts=True),
            section("Payments", [col("name", "Method"), col("amount", "Amount", "money"), col("reference", "Reference")], tender_rows_out, "No payment recorded."),
            section("Returns against this bill", [col("at", "When", "datetime"), col("person", "Taken by", link="person", params=PERSON_LINK, carry=False),
                                                  col("method", "Paid back as"), col("item", "Item", link="item", params=ITEM_LINK, carry=False),
                                                  col("qty", "Units", "number"), col("value", "Value", "money"), col("total", "Refund total", "money")],
                    [{"at": iso_at(r["at"]), "userId": str(r["user_id"]), "person": users.get(str(r["user_id"]), "-"), "method": (r["method"] or "").capitalize(),
                      **({"productId": None, "item": "A pharmacy Item"} if (r["department"] or "").strip().upper() in unseen
                         else {"productId": str(r["product_id"]) if r["product_id"] else None, "item": r["item"] or "-"}),
                      "qty": units(r["qty"]), "value": money(r["value"]), "total": money(r["total"])} for r in refunds],
                    "Nothing has come back against this bill."),
        ],
        notes=["Sales on a line are after its discount and before GST; the line total adds the GST. Gross profit takes off the line's cost when sold (the Item's average cost for older bills).",
               "Value on a return is units times what the customer paid for each, GST included."],
    )


def _json_list(value) -> list:
    """A JSON column as SQL hands it back: text, already parsed, or nothing."""
    import json

    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value) if value else []
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


VIEWS = {
    "sales": _sales, "gross-profit": _gross_profit, "bills": _bills, "average-bill": _average_bill, "items-per-bill": _items_per_bill,
    "returns": _returns, "discounts": _discounts, "payment-mix": _payment_mix, "busy-hours": _busy_hours, "tills": _tills,
    "departments": lambda ctx: _groups(ctx, "departments"), "categories": lambda ctx: _groups(ctx, "categories"),
    "brands": lambda ctx: _groups(ctx, "brands"), "top-items": _top_items, "bottom-items": _bottom_items, "staff": _staff,
    "stock-value": _stock_value, "low-stock": _low_stock, "near-expiry": _near_expiry, "not-sold-90": _not_sold_90,
    "day": _day, "group": _group, "item": _item, "person": _person, "item-person": _item_person, "bill": _bill,
}


async def analysis_item(period: Period, product_id: str, root: str | None = None) -> dict:
    """An Item's drill-down for ABC and XYZ: its buckets over the period as a chart, where it ranks, who sold it,
    which days and its bills."""
    ctx = Ctx(period=period, root=root if root in BOARD else "top-items", product_id=product_id)
    await ctx.item_name(product_id)
    classes = await an.item_classes(period, product_id)
    plan = an.bucket_plan(period)
    sql, params = an.lines_sql(period, Scope(product_id=product_id))
    got = {str(r["bucket"]): r for r in await q(f"""
        WITH l AS ({sql}), d AS (SELECT {SQL_DAY} AS day, qty, sales FROM l)
        SELECT {plan.sql_key} AS bucket, SUM(qty) AS units, SUM(sales) AS sales FROM d GROUP BY bucket
    """, params)}
    series = [{"day": b.start.isoformat(), "value": units(got.get(b.key, {}).get("units")), "label": b.label} for b in plan.buckets]
    lead = []
    notes = []
    if classes:
        where = {"X": "steady", "Y": "up and down", "Z": "now and then"}.get(classes["xyzClass"] or "", None)
        facts = [
            {"label": "ABC class (by sales value)", "value": f"{classes['abcClass']}, ranked {classes['rank']} of {classes['itemsRanked']}"},
            {"label": "Share of the period's sales", "value": f"{classes['sharePct']:.2f}%"},
            {"label": "How steadily it sells", "value": (f"{classes['xyzClass']}: {where}, varies by {classes['cv'] * 100:.0f}% of its average" if where
                                                         else "Too new to tell")},
            {"label": f"Units {plan.per} on average", "value": qty_text(classes["perBucket"]) if classes["perBucket"] is not None else "-"},
            {"label": f"{plan.kind.capitalize()}s it sold in", "value": f"{classes['bucketsSold']} of {classes['buckets']}"},
        ]
        if classes["cell"]:
            facts.append({"label": "What to do", "value": an.ADVICE[classes["cell"]]})
        lead.append(section("Where it stands", [col("label", "What"), col("value", "Detail")], facts, "-", facts=True))
        if classes["since"]:
            notes.append(f"It first came into the branch on {day_label(an.as_day(classes['since']))}, so it is judged from then.")
    else:
        notes.append("It didn't sell in this period, so it has no ABC or XYZ class.")
    out = await item_view(ctx, "item", extra_notes=notes, series=series, series_label=f"Units sold each {plan.kind}", series_kind="bars", lead=lead)
    out["analysis"] = classes
    out["bucket"] = plan.kind
    return out
