"""ABC and XYZ at the branch: which Items bring the money in, which sell steadily, and which hardly sell.

* **ABC** ranks Items by what they bring in over a period (sales value, gross profit or units). Walking from the
  biggest down, an Item is A while the share of everything above it is under 80%, B while it is under 95%, and C
  after that. Head office uses the same rule, so a branch's A list and head office's read the same way.
* **XYZ** says how steadily an Item sells: the coefficient of variation of the units it sold per day, week or month
  (days for a month or less, weeks up to 184 days, months beyond, counted back from the period's last day, the same
  as head office). X is 0.5 or less, Y up to 1.0, Z above. Buckets before the Item first came into the branch aren't
  counted, so an Item that arrived last month isn't judged on the months before it; with fewer than eight buckets
  there is nothing honest to say, and it is "Too new to tell".
* **Sold least** is the other end: every Item on the shelf or sold in the period, least sold first, with what is on
  hand and how many days that would last.

Everything is added up in SQL from bills, their lines and the Returns screen; only per-Item totals come back to
Python. Sales are net of discounts and of returns (goods handed back on a bill and refunds taken on the Returns
screen), before GST. A line's own discount and cost are used where the line recorded them; older lines share the
bill discount by value and fall back to the Item's average cost, the same way the Sale Summary does.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from tortoise import Tortoise

PKT = timezone(timedelta(hours=5))

A_CUT = 0.80
B_CUT = 0.95
X_CUT = 0.5
Y_CUT = 1.0
MIN_BUCKETS = 8

# The label a group of Items gets when they have no department, category or brand filled in.
NO_GROUP = "~none"
GROUP_COLUMNS = ("department", "category", "brand")
BASES = ("sales", "profit", "units")

# The SQL IN lists below are chunked, so a period with thousands of Items never runs into SQLite's variable limit.
CHUNK = 500


class AnalysisError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


async def q(sql: str, params: list | None = None) -> list[dict]:
    return await Tortoise.get_connection("default").execute_query_dict(sql, params or [])


def shop_today() -> date:
    return datetime.now(PKT).date()


# ── periods ─────────────────────────────────────────────────────────────────────────────────────

PERIODS: tuple[tuple[str, str], ...] = (
    ("today", "Today"),
    ("last7", "Last 7 days"),
    ("last30", "Last 30 days"),
    ("thisMonth", "This month"),
    ("lastMonth", "Last month"),
    ("last3m", "Last 3 months"),
    ("last6m", "Last 6 months"),
    ("thisYear", "This year"),
)
PERIOD_LABELS = dict(PERIODS)


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def day_label(day: date, year: bool = True) -> str:
    return f"{day:%a} {day.day} {day:%b}" + (f" {day.year}" if year else "")


def span_label(start: date, end: date) -> str:
    if start == end:
        return day_label(start)
    if start.year == end.year:
        return f"{start.day} {start:%b} to {end.day} {end:%b} {end.year}"
    return f"{start.day} {start:%b} {start.year} to {end.day} {end:%b} {end.year}"


def bound(day: date) -> str:
    """The instant a shop day starts, written the way bills store their time, so the database compares text with
    text and can use the index on it. Bills are kept in UTC; the shop's day starts at 19:00 UTC the evening before."""
    return datetime.combine(day, time(), PKT).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")


@dataclass(frozen=True)
class Period:
    id: str
    label: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def start_at(self) -> str:
        return bound(self.start)

    @property
    def end_at(self) -> str:
        """Exclusive: the start of the day after the last one."""
        return bound(self.end + timedelta(days=1))

    def previous(self) -> "Period":
        end = self.start - timedelta(days=1)
        start = end - timedelta(days=self.days - 1)
        return Period("previous", span_label(start, end), start, end)

    def out(self) -> dict:
        return {
            "id": self.id, "label": self.label, "start": self.start.isoformat(), "end": self.end.isoformat(),
            "days": self.days, "range": span_label(self.start, self.end),
            "compareLabel": f"against {span_label(*_prev_span(self))}",
        }


def _prev_span(p: Period) -> tuple[date, date]:
    prev = p.previous()
    return prev.start, prev.end


def parse_day(value: str | None, what: str) -> date:
    if not value:
        raise AnalysisError(f"Pick the {what} date.")
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        raise AnalysisError(f"The {what} date isn't a date. Pick it from the calendar.") from None


def resolve_period(period: str | None, from_: str | None = None, to: str | None = None, default: str = "last30") -> Period:
    """A period the screens name (`last6m`) or a custom one (`custom` with `from` and `to` as shop days)."""
    today = shop_today()
    pid = period or default
    if pid == "custom" or (not period and (from_ or to)):
        start, end = parse_day(from_, "start"), parse_day(to, "end")
        if end < start:
            raise AnalysisError("The end date is before the start date. Swap them round.")
        if (end - start).days > 366 * 5:
            raise AnalysisError("Pick a period of five years or less.")
        return Period("custom", span_label(start, end), start, end)
    if pid == "today":
        start, end = today, today
    elif pid == "last7":
        start, end = today - timedelta(days=6), today
    elif pid == "last30":
        start, end = today - timedelta(days=29), today
    elif pid == "thisMonth":
        start, end = today.replace(day=1), today
    elif pid == "lastMonth":
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    elif pid == "last3m":
        start, end = _add_months(today, -3) + timedelta(days=1), today
    elif pid == "last6m":
        start, end = _add_months(today, -6) + timedelta(days=1), today
    elif pid == "thisYear":
        start, end = today.replace(month=1, day=1), today
    else:
        raise AnalysisError("That period isn't one this report knows. Pick one from the list.")
    return Period(pid, PERIOD_LABELS[pid], start, end)


def periods_out() -> list[dict]:
    return [{"id": pid, "label": label} for pid, label in PERIODS] + [{"id": "custom", "label": "Pick dates"}]


# ── the lines every figure is added up from ─────────────────────────────────────────────────────

@dataclass
class Scope:
    """Which part of a period's trade to add up: one day, one Item, one person, or Items of one department,
    category, brand or supplier. Anything left None isn't narrowed."""
    day: date | None = None
    product_id: str | None = None
    user_id: str | None = None
    department: str | None = None
    category: str | None = None
    brand: str | None = None
    supplier_id: str | None = None


# A bill line, signed: goods handed back on a bill count against it.
_SALE_QTY = "(CASE WHEN sl.is_return = 1 THEN -1.0 ELSE 1.0 END) * CAST(sl.qty AS REAL)"
_SALE_VALUE = f"({_SALE_QTY}) * CAST(sl.unit_price AS REAL)"
# The line's own discount, else its share of the bill discount by value (sales from before lines kept it).
_SALE_DISC = (
    f"COALESCE(CAST(sl.disc_amount AS REAL), CASE WHEN CAST(s.gross AS REAL) <> 0 "
    f"THEN CAST(s.disc_total AS REAL) * {_SALE_VALUE} / CAST(s.gross AS REAL) ELSE 0 END)"
)
_SALE_COST = f"({_SALE_QTY}) * COALESCE(CAST(sl.unit_cost AS REAL), CAST(p.avg_cost AS REAL), 0)"
# A refund on the Returns screen. Its unit price is what the customer paid, GST included; the GST inside is taken
# off so it nets against sales that are counted before GST. Older refunds recorded no GST and are taken as they are.
_RET_QTY = "CAST(rl.qty AS REAL)"
_RET_VALUE = f"({_RET_QTY} * CAST(rl.unit_price AS REAL) - COALESCE(CAST(rl.tax_amount AS REAL), 0))"
_RET_COST = f"{_RET_QTY} * COALESCE(CAST(rl.unit_cost AS REAL), CAST(p.avg_cost AS REAL), 0)"

SUPPLIER_ITEMS = (
    "SELECT ps.product_id FROM product_suppliers ps WHERE ps.supplier_id = ? "
    "UNION SELECT gl.product_id FROM grn_lines gl JOIN grns g ON g.id = gl.grn_id WHERE g.supplier_id = ?"
)


def group_condition(alias: str, column: str, value: str) -> tuple[str, list]:
    if column not in GROUP_COLUMNS:
        raise AnalysisError("That grouping isn't one this report knows.")
    if value == NO_GROUP:
        return f"COALESCE({alias}.{column}, '') = ''", []
    return f"{alias}.{column} = ?", [value]


def lines_sql(period: Period, scope: Scope | None = None) -> tuple[str, list]:
    """One row per bill line and per Returns-screen line in the period, signed, before GST.

    Columns: product_id, sale_id (the bill, or the bill a refund was against), invoice, at, user_id (who rang the bill
    or took the refund), source ('bill' or 'returns'), returned (1 for goods coming back), qty, sales, cost."""
    scope = scope or Scope()
    start, end = period.start_at, period.end_at
    if scope.day:
        start, end = bound(scope.day), bound(scope.day + timedelta(days=1))
    sale_where, sale_params = ["s.at >= ?", "s.at < ?"], [start, end]
    ret_where, ret_params = ["r.at >= ?", "r.at < ?"], [start, end]

    def both(on_sale: str, on_return: str, params: list) -> None:
        sale_where.append(on_sale)
        sale_params.extend(params)
        ret_where.append(on_return)
        ret_params.extend(params)

    if scope.product_id:
        both("sl.product_id = ?", "rl.product_id = ?", [scope.product_id])
    if scope.user_id:
        both("s.cashier_id = ?", "r.cashier_id = ?", [scope.user_id])
    for column in GROUP_COLUMNS:
        value = getattr(scope, column)
        if value is not None:
            cond, params = group_condition("p", column, value)
            both(cond, cond, params)
    if scope.supplier_id:
        both(f"p.id IN ({SUPPLIER_ITEMS})", f"p.id IN ({SUPPLIER_ITEMS})", [scope.supplier_id, scope.supplier_id])

    sql = f"""
        SELECT sl.product_id AS product_id, s.id AS sale_id, s.invoice_number AS invoice, s.at AS at,
               s.cashier_id AS user_id, 'bill' AS source, sl.is_return AS returned,
               {_SALE_QTY} AS qty, {_SALE_VALUE} - {_SALE_DISC} AS sales, {_SALE_COST} AS cost
        FROM sale_lines sl
        JOIN sale_records s ON s.id = sl.sale_id
        JOIN products p ON p.id = sl.product_id
        WHERE {' AND '.join(sale_where)}
        UNION ALL
        SELECT rl.product_id, r.against_id, sa.invoice_number, r.at,
               r.cashier_id, 'returns', 1,
               -{_RET_QTY}, -{_RET_VALUE}, -{_RET_COST}
        FROM return_lines rl
        JOIN return_records r ON r.id = rl.return_record_id
        JOIN products p ON p.id = rl.product_id
        LEFT JOIN sale_records sa ON sa.id = r.against_id
        WHERE {' AND '.join(ret_where)}
    """
    return sql, sale_params + ret_params


# The shop day and hour of a row's time, in SQL.
SQL_DAY = "date(at, '+5 hours')"
SQL_HOUR = "CAST(strftime('%H', at, '+5 hours') AS INTEGER)"


def f(value) -> float:
    return float(value) if value is not None else 0.0


def money(value) -> str:
    return format(Decimal(str(round(f(value), 2))).quantize(Decimal("0.01")), "f")


def units(value) -> str:
    text = format(Decimal(str(round(f(value), 3))).normalize(), "f")
    return "0" if text in ("-0", "") else text


def pct(part, whole) -> float | None:
    return round(f(part) / f(whole) * 100, 1) if f(whole) else None


def as_day(text) -> date | None:
    return date.fromisoformat(str(text)[:10]) if text else None


async def products_by_id(ids, columns: str = "id, sku, name, department, category, brand, avg_cost, price") -> dict[str, dict]:
    ids = [i for i in dict.fromkeys(ids) if i]
    out: dict[str, dict] = {}
    for n in range(0, len(ids), CHUNK):
        part = ids[n:n + CHUNK]
        for row in await q(f"SELECT {columns} FROM products WHERE id IN ({','.join('?' for _ in part)})", part):
            out[str(row["id"])] = row
    return out


async def first_seen(ids) -> dict[str, date]:
    """The shop day each Item first came into the branch: its first stock movement of any kind, which is the
    earlier of its first delivery and its first sale (every sale moves stock)."""
    ids = [i for i in dict.fromkeys(ids) if i]
    out: dict[str, date] = {}
    for n in range(0, len(ids), CHUNK):
        part = ids[n:n + CHUNK]
        rows = await q(
            f"SELECT product_id, date(MIN(at), '+5 hours') AS first_day FROM stock_movements "
            f"WHERE product_id IN ({','.join('?' for _ in part)}) GROUP BY product_id", part,
        )
        for row in rows:
            if row["first_day"]:
                out[str(row["product_id"])] = as_day(row["first_day"])
    return out


async def on_hand(ids) -> dict[str, float]:
    ids = [i for i in dict.fromkeys(ids) if i]
    out: dict[str, float] = {}
    for n in range(0, len(ids), CHUNK):
        part = ids[n:n + CHUNK]
        rows = await q(
            f"SELECT product_id, SUM(CAST(qty AS REAL)) AS qty FROM stock_movements "
            f"WHERE product_id IN ({','.join('?' for _ in part)}) GROUP BY product_id", part,
        )
        out.update({str(r["product_id"]): f(r["qty"]) for r in rows})
    return out


async def last_sold(ids, before: str | None = None) -> dict[str, str]:
    """When each Item was last on a bill (not handed back), up to `before`."""
    ids = [i for i in dict.fromkeys(ids) if i]
    out: dict[str, str] = {}
    for n in range(0, len(ids), CHUNK):
        part = ids[n:n + CHUNK]
        where = "" if before is None else " AND s.at < ?"
        rows = await q(
            f"SELECT sl.product_id AS product_id, MAX(s.at) AS last_at FROM sale_lines sl JOIN sale_records s ON s.id = sl.sale_id "
            f"WHERE sl.product_id IN ({','.join('?' for _ in part)}) AND sl.is_return = 0{where} GROUP BY sl.product_id",
            part + ([] if before is None else [before]),
        )
        out.update({str(r["product_id"]): iso_at(r["last_at"]) for r in rows})
    return out


async def suppliers_of(ids) -> dict[str, str]:
    """An Item's first-choice supplier from the Item form, else whoever delivered it last."""
    ids = [i for i in dict.fromkeys(ids) if i]
    out: dict[str, str] = {}
    for n in range(0, len(ids), CHUNK):
        part = ids[n:n + CHUNK]
        marks = ",".join("?" for _ in part)
        for row in await q(
            f"SELECT ps.product_id, s.name FROM product_suppliers ps JOIN suppliers s ON s.id = ps.supplier_id "
            f"WHERE ps.product_id IN ({marks}) ORDER BY ps.priority", part,
        ):
            out.setdefault(str(row["product_id"]), row["name"])
        missing = [i for i in part if i not in out]
        if missing:
            for row in await q(
                f"SELECT gl.product_id, s.name FROM grn_lines gl JOIN grns g ON g.id = gl.grn_id JOIN suppliers s ON s.id = g.supplier_id "
                f"WHERE gl.product_id IN ({','.join('?' for _ in missing)}) ORDER BY g.at DESC", missing,
            ):
                out.setdefault(str(row["product_id"]), row["name"])
    return out


def iso_at(value) -> str | None:
    """A stored time as ISO with its UTC offset, for the app to show in local time."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return str(value).replace(" ", "T", 1)


# ── buckets for XYZ ─────────────────────────────────────────────────────────────────────────────
# The same rules head office uses (cloud analytics_service.bucket_kind / bucket_index), so a branch and head office
# call the same Item steady or not.

@dataclass
class Bucket:
    index: int  # 0 is the latest
    start: date
    end: date

    @property
    def key(self) -> str:
        return str(self.index)

    @property
    def label(self) -> str:
        if self.start == self.end:
            return f"{self.start.day} {self.start:%b}"
        return f"{self.start.day} {self.start:%b} to {self.end.day} {self.end:%b}"


@dataclass
class BucketPlan:
    kind: str  # day, week, month
    end: date
    buckets: list[Bucket]  # oldest first
    sql_key: str  # the bucket index of a `day` column, in SQL

    @property
    def per(self) -> str:
        return {"day": "a day", "week": "a week", "month": "a month"}[self.kind]


def bucket_kind(period: Period) -> str:
    """Days for a month or less; weeks up to 184 days, the longest six calendar months can be, so Last 6 months
    always counts in weeks; months beyond."""
    if period.days <= 31:
        return "day"
    if period.days <= 184:
        return "week"
    return "month"


def bucket_index(day: date, end: date, kind: str) -> int:
    """Which bucket a day falls in, counting back from the period's last day (0 is the latest), so the newest
    bucket is always whole: a week is the 7 days ending on the last day, then the 7 before; a month runs from the
    day after that date a month earlier (17 Aug to 16 Sep)."""
    if kind == "day":
        return (end - day).days
    if kind == "week":
        return (end - day).days // 7
    k = (end.year - day.year) * 12 + end.month - day.month
    return k - 1 if day > _add_months(end, -k) else k


def _bucket_span(index: int, end: date, kind: str) -> tuple[date, date]:
    if kind == "day":
        d = end - timedelta(days=index)
        return d, d
    if kind == "week":
        last = end - timedelta(days=7 * index)
        return last - timedelta(days=6), last
    return _add_months(end, -(index + 1)) + timedelta(days=1), _add_months(end, -index)


def bucket_plan(period: Period) -> BucketPlan:
    kind = bucket_kind(period)
    count = bucket_index(period.start, period.end, kind) + 1
    buckets = []
    for i in reversed(range(count)):
        first, last = _bucket_span(i, period.end, kind)
        buckets.append(Bucket(i, max(first, period.start), last))
    end = period.end.isoformat()
    if kind == "day":
        key = f"CAST(julianday('{end}') - julianday(day) AS INTEGER)"
    elif kind == "week":
        key = f"CAST((julianday('{end}') - julianday(day)) / 7 AS INTEGER)"
    else:
        # Months aren't a fixed number of days, so each month's first day is named; at most 60 over five years.
        whens = " ".join(f"WHEN day >= '{b.start.isoformat()}' THEN {b.index}" for b in sorted(buckets, key=lambda b: b.index))
        key = f"CASE {whens} ELSE {count - 1} END"
    return BucketPlan(kind, period.end, buckets, key)


def xyz_of(plan: BucketPlan, demand: dict[int, float], net_units: float, starts: date) -> dict:
    """X, Y or Z from the units sold in each bucket, empty buckets included, from the bucket the Item came in. A
    day on which more came back than sold adds nothing (demand isn't negative). Fewer than eight buckets, or nothing
    sold, is too new to tell."""
    n = bucket_index(starts, plan.end, plan.kind) + 1
    series = [demand.get(i, 0.0) for i in range(n)]
    sold = sum(1 for x in series if x > 0)
    per = round(net_units / n, 2) if n else None
    mean = sum(series) / n if n else 0.0
    if n < MIN_BUCKETS or mean <= 0:
        return {"xyzClass": "new", "cv": None, "buckets": n, "bucketsSold": sold, "perBucket": per}
    cv = math.sqrt(sum((x - mean) ** 2 for x in series) / n) / mean
    klass = "X" if cv <= X_CUT else ("Y" if cv <= Y_CUT else "Z")
    return {"xyzClass": klass, "cv": round(cv, 3), "buckets": n, "bucketsSold": sold, "perBucket": per}


# ── ABC and XYZ together ────────────────────────────────────────────────────────────────────────

ADVICE = {
    "AX": "Best sellers that sell every week: never let them run out.",
    "AY": "Big earners that go up and down: keep extra stock ahead of busy spells.",
    "AZ": "Big earners that sell in bursts: watch them closely and order little and often.",
    "BX": "Steady middle sellers: reorder on a fixed routine.",
    "BY": "Middle sellers that go up and down: check their stock every week.",
    "BZ": "Middle sellers that come and go: keep a small stock and reorder when they sell.",
    "CX": "Small but steady: keep a little on the shelf, they don't need watching.",
    "CY": "Small sellers that go up and down: order small amounts, less often.",
    "CZ": "Rarely sold and small: think about dropping or ordering only on request.",
}
NEW_ADVICE = {
    "A": "Earning well but too new to judge: check again once they have a couple of months behind them.",
    "B": "Too new to judge how steadily they sell: keep an eye on them.",
    "C": "New and small so far: give them time before deciding.",
}
BASIS_LABEL = {"sales": "sales value", "profit": "gross profit", "units": "units sold"}


def scope_from(department: str | None, category: str | None, brand: str | None, supplier_id: str | None) -> Scope:
    clean = lambda v: v if v not in (None, "") else None  # noqa: E731
    return Scope(department=clean(department), category=clean(category), brand=clean(brand), supplier_id=clean(supplier_id))


async def classify(period: Period, basis: str, scope: Scope) -> dict:
    if basis not in BASES:
        raise AnalysisError("Rank by sales value, gross profit or units.")
    plan = bucket_plan(period)
    sql, params = lines_sql(period, scope)
    # Per Item per shop day first, so a day on which more came back than sold can be left out of demand, then per
    # bucket: only a few rows per Item come back, however long the period.
    rows = await q(f"""
        WITH l AS ({sql}),
        d AS (
            SELECT product_id, {SQL_DAY} AS day, SUM(qty) AS units, SUM(sales) AS sales, SUM(cost) AS cost,
                   COUNT(DISTINCT CASE WHEN source = 'bill' AND returned = 0 THEN sale_id END) AS bills
            FROM l GROUP BY product_id, day
        )
        SELECT product_id, {plan.sql_key} AS bucket, SUM(units) AS units,
               SUM(CASE WHEN units > 0 THEN units ELSE 0 END) AS demand,
               SUM(sales) AS sales, SUM(cost) AS cost, SUM(bills) AS bills
        FROM d GROUP BY product_id, bucket
    """, params)

    items: dict[str, dict] = {}
    for r in rows:
        pid = str(r["product_id"])
        it = items.setdefault(pid, {"units": 0.0, "sales": 0.0, "cost": 0.0, "bills": 0, "demand": {}})
        it["units"] += f(r["units"])
        it["sales"] += f(r["sales"])
        it["cost"] += f(r["cost"])
        it["bills"] += int(r["bills"] or 0)
        bucket = int(r["bucket"])
        it["demand"][bucket] = it["demand"].get(bucket, 0.0) + f(r["demand"])

    info = await products_by_id(items.keys(), "id, sku, name, department, category, brand")
    seen = await first_seen(items.keys())

    out = []
    for pid, it in items.items():
        p = info.get(pid, {})
        value = {"sales": it["sales"], "profit": it["sales"] - it["cost"], "units": it["units"]}[basis]
        starts = max(period.start, seen.get(pid, period.start))
        out.append({
            "productId": pid, "sku": p.get("sku"), "name": p.get("name") or pid,
            "department": p.get("department"), "category": p.get("category"), "brand": p.get("brand"),
            "_value": value, "_units": it["units"], "_sales": it["sales"], "_profit": it["sales"] - it["cost"],
            "bills": it["bills"], "since": starts.isoformat() if starts > period.start else None,
            **xyz_of(plan, it["demand"], it["units"], starts),
        })

    out.sort(key=lambda r: (-r["_value"], -r["_sales"], r["name"]))
    total = sum(r["_value"] for r in out if r["_value"] > 0)
    total_sales = sum(r["_sales"] for r in out if r["_sales"] > 0)
    running = 0.0
    for rank, r in enumerate(out, start=1):
        before = running / total if total > 0 else 1.0
        if r["_value"] <= 0:
            klass = "C"
        else:
            klass = "A" if before < A_CUT else ("B" if before < B_CUT else "C")
            running += r["_value"]
        r.update({
            "rank": rank, "abcClass": klass,
            "sharePct": round(r["_value"] / total * 100, 2) if total > 0 and r["_value"] > 0 else 0.0,
            "shareBeforePct": round(before * 100, 2),
            "cumulativePct": round(running / total * 100, 2) if total > 0 else 0.0,
            "cell": f"{klass}{r['xyzClass']}" if r["xyzClass"] in ("X", "Y", "Z") else None,
        })
    return {"plan": plan, "items": out, "total": total, "totalSales": total_sales}


def _row_out(r: dict, basis: str = "sales") -> dict:
    return {
        k: v for k, v in r.items() if not k.startswith("_")
    } | {
        "value": units(r["_value"]) if basis == "units" else money(r["_value"]),
        "units": units(r["_units"]), "sales": money(r["_sales"]), "profit": money(r["_profit"]),
        "marginPct": pct(r["_profit"], r["_sales"]) if r["_sales"] > 0 else None,
    }


def _summary(result: dict, period: Period, basis: str) -> dict:
    items = result["items"]
    total_sales = result["totalSales"]
    classes = []
    for k in ("A", "B", "C"):
        rows = [r for r in items if r["abcClass"] == k]
        value = sum(r["_value"] for r in rows if r["_value"] > 0)
        classes.append({
            "class": k, "items": len(rows), "value": money(value) if basis != "units" else units(value),
            "sales": money(sum(r["_sales"] for r in rows)),
            "sharePct": pct(value, result["total"]) or 0.0,
            "itemsPct": pct(len(rows), len(items)) or 0.0,
        })
    xyz = []
    for k, label in (("X", "Steady"), ("Y", "Up and down"), ("Z", "Now and then"), ("new", "Too new to tell")):
        rows = [r for r in items if r["xyzClass"] == k]
        xyz.append({
            "class": k, "label": label, "items": len(rows),
            "sales": money(sum(r["_sales"] for r in rows)), "salesPct": pct(sum(r["_sales"] for r in rows if r["_sales"] > 0), total_sales) or 0.0,
        })
    plan = result["plan"]
    return {
        "period": period.out(), "basis": basis, "basisLabel": BASIS_LABEL[basis],
        "bucket": plan.kind, "bucketsInPeriod": len(plan.buckets),
        "itemsSold": len(items), "total": money(result["total"]) if basis != "units" else units(result["total"]),
        "totalSales": money(total_sales), "classes": classes, "xyz": xyz,
    }


def _notes(period: Period, basis: str, plan: BucketPlan) -> list[str]:
    notes = [
        f"Items are ranked by {BASIS_LABEL[basis]} from the biggest down. An Item is A while everything above it adds up to less than 80% of the total, B while it is under 95%, and C after that.",
        "Sales are after discounts and before GST, less goods handed back on a bill and refunds taken on the Returns screen. Gross profit takes off each line's cost when sold (the Item's average cost for older sales).",
        f"How steadily an Item sells is judged on the units it sold each {plan.kind} of this period ({len(plan.buckets)} in all), counted back from the last day so the newest {plan.kind} is always a whole one. X means its sales vary by half their average or less, Y by up to their average, Z by more than that.",
        f"{plan.kind.capitalize()}s before an Item first came into the branch aren't counted, and it needs at least {MIN_BUCKETS} {plan.kind}s. Fewer than that, or nothing sold on balance, is marked Too new to tell. Head office works this out the same way.",
    ]
    if len(plan.buckets) < MIN_BUCKETS:
        notes.insert(0, f"This period has only {len(plan.buckets)} {plan.kind}s, so no Item can be judged on how steadily it sells. Pick Last 30 days or longer.")
    return notes


def _page(rows: list[dict], limit: int, offset: int) -> list[dict]:
    limit = max(1, min(limit, 500))
    return rows[max(0, offset):max(0, offset) + limit]


def _search(rows: list[dict], text: str | None) -> list[dict]:
    if not text:
        return rows
    t = text.strip().lower()
    return [r for r in rows if t in (r["name"] or "").lower() or t in (r["sku"] or "").lower()]


async def abc(period: Period, basis: str, scope: Scope, klass: str | None, xyz: str | None, cell: str | None,
              search: str | None, limit: int, offset: int) -> dict:
    result = await classify(period, basis, scope)
    rows = result["items"]
    if klass:
        rows = [r for r in rows if r["abcClass"] == klass.upper()]
    if xyz:
        rows = [r for r in rows if (r["xyzClass"] or "none") == (xyz if xyz == "new" else xyz.upper())]
    if cell:
        rows = [r for r in rows if r["cell"] == cell.upper() or (cell.lower().endswith("new") and r["abcClass"] == cell[0].upper() and r["xyzClass"] == "new")]
    rows = _search(rows, search)
    return {
        **_summary(result, period, basis), "count": len(rows),
        "items": [_row_out(r, basis) for r in _page(rows, limit, offset)],
        "notes": _notes(period, basis, result["plan"]),
    }


async def xyz(period: Period, basis: str, scope: Scope, klass: str | None, search: str | None, limit: int, offset: int) -> dict:
    result = await classify(period, basis, scope)
    order = {"X": 0, "Y": 1, "Z": 2, "new": 3, None: 4}
    rows = sorted(result["items"], key=lambda r: (order[r["xyzClass"]], r["cv"] if r["cv"] is not None else 0, -r["_sales"]))
    if klass:
        rows = [r for r in rows if (r["xyzClass"] or "none") == (klass if klass in ("new", "none") else klass.upper())]
    rows = _search(rows, search)
    return {
        **_summary(result, period, basis), "count": len(rows),
        "items": [_row_out(r, basis) for r in _page(rows, limit, offset)],
        "notes": _notes(period, basis, result["plan"]),
    }


async def matrix(period: Period, basis: str, scope: Scope) -> dict:
    result = await classify(period, basis, scope)
    items = result["items"]
    total_sales = result["totalSales"]
    cells = []
    for a in ("A", "B", "C"):
        for x in ("X", "Y", "Z", "new"):
            rows = [r for r in items if r["abcClass"] == a and r["xyzClass"] == x]
            sales = sum(r["_sales"] for r in rows)
            top = sorted(rows, key=lambda r: -r["_sales"])[:3]
            cells.append({
                "cell": f"{a}{x}", "abcClass": a, "xyzClass": x, "items": len(rows),
                "sales": money(sales), "salesPct": pct(max(sales, 0), total_sales) or 0.0,
                "advice": ADVICE[f"{a}{x}"] if x != "new" else NEW_ADVICE[a],
                "examples": [r["name"] for r in top],
            })
    return {**_summary(result, period, basis), "cells": cells, "notes": _notes(period, basis, result["plan"])}


# ── sold least ──────────────────────────────────────────────────────────────────────────────────

SOLD_LEAST_SORTS = {
    "units": "units ASC, stock_value DESC, sales ASC, p.name",
    "stockValue": "stock_value DESC, units ASC, p.name",
    "cover": "CASE WHEN units > 0 THEN on_hand / units ELSE 1e18 END DESC, stock_value DESC, p.name",
}


async def sold_least(period: Period, scope: Scope, search: str | None, sort: str, limit: int, offset: int, unsold_only: bool = False) -> dict:
    """Every Item that is on the shelf or sold in the period, least sold first. An Item with stock that didn't sell
    at all is the first thing this list is for, so those come before anything that sold even once."""
    if sort not in SOLD_LEAST_SORTS:
        sort = "units"
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    sql, params = lines_sql(period)
    where, where_params = ["(COALESCE(oh.on_hand, 0) > 0 OR sold.product_id IS NOT NULL)"], []
    for column in GROUP_COLUMNS:
        value = getattr(scope, column)
        if value is not None:
            cond, cparams = group_condition("p", column, value)
            where.append(cond)
            where_params.extend(cparams)
    if scope.supplier_id:
        where.append(f"p.id IN ({SUPPLIER_ITEMS})")
        where_params.extend([scope.supplier_id, scope.supplier_id])
    if search and search.strip():
        where.append("(p.name LIKE ? OR p.sku LIKE ? OR p.barcode = ?)")
        like = f"%{search.strip()}%"
        where_params.extend([like, like, search.strip()])
    if unsold_only:
        where.append("COALESCE(sold.units, 0) <= 0")

    rows = await q(f"""
        WITH l AS ({sql}),
        sold AS (
            SELECT product_id, SUM(qty) AS units, SUM(sales) AS sales,
                   COUNT(DISTINCT CASE WHEN source = 'bill' AND returned = 0 THEN sale_id END) AS bills
            FROM l GROUP BY product_id
        ),
        oh AS (SELECT product_id, SUM(CAST(qty AS REAL)) AS on_hand FROM stock_movements GROUP BY product_id),
        listed AS (
            SELECT p.id AS id, p.sku AS sku, p.name AS name, p.department AS department, p.category AS category,
                   p.brand AS brand, CAST(p.avg_cost AS REAL) AS avg_cost,
                   COALESCE(sold.units, 0) AS units, COALESCE(sold.sales, 0) AS sales, COALESCE(sold.bills, 0) AS bills,
                   COALESCE(oh.on_hand, 0) AS on_hand,
                   MAX(COALESCE(oh.on_hand, 0), 0) * COALESCE(CAST(p.avg_cost AS REAL), 0) AS stock_value
            FROM products p
            LEFT JOIN oh ON oh.product_id = p.id
            LEFT JOIN sold ON sold.product_id = p.id
            WHERE {' AND '.join(where)}
        )
        SELECT p.*,
               COUNT(*) OVER () AS total_rows,
               SUM(CASE WHEN units <= 0 THEN 1 ELSE 0 END) OVER () AS unsold_rows,
               SUM(CASE WHEN units <= 0 THEN stock_value ELSE 0 END) OVER () AS unsold_value
        FROM listed p
        ORDER BY {SOLD_LEAST_SORTS[sort]}
        LIMIT ? OFFSET ?
    """, params + where_params + [limit, offset])

    ids = [str(r["id"]) for r in rows]
    last = await last_sold(ids, period.end_at)
    suppliers = await suppliers_of(ids)
    total = rows[0]["total_rows"] if rows else 0
    if not rows and offset:
        # Asked for a page past the end: still say how many there are.
        total = (await sold_least(period, scope, search, sort, 1, 0, unsold_only))["count"]

    out = []
    for r in rows:
        pid = str(r["id"])
        per_day = f(r["units"]) / period.days
        cover = f(r["on_hand"]) / per_day if per_day > 0 and f(r["on_hand"]) > 0 else None
        out.append({
            "productId": pid, "sku": r["sku"], "name": r["name"],
            "department": r["department"], "category": r["category"], "brand": r["brand"], "supplier": suppliers.get(pid),
            "units": units(r["units"]), "sales": money(r["sales"]), "bills": int(r["bills"] or 0),
            "lastSold": last.get(pid), "onHand": units(r["on_hand"]), "stockValue": money(r["stock_value"]),
            "daysOfCover": round(cover) if cover is not None else None,
        })
    return {
        "period": period.out(), "count": total, "items": out, "sort": sort,
        "unsold": int(rows[0]["unsold_rows"] or 0) if rows else 0,
        "unsoldValue": money(rows[0]["unsold_value"]) if rows else "0.00",
        "notes": [
            "Every Item with stock on hand or a sale in this period is listed, least sold first. Items that didn't sell at all come first, with the most stock money on the shelf at the top.",
            "Units are net of returns. Days of cover is how long today's stock would last selling at this period's daily rate.",
            "Last sold is the last bill the Item was on, up to the end of the period.",
        ],
    }


async def options() -> dict:
    """What the filters offer: departments, categories and brands in use, and suppliers."""
    def names(column: str) -> str:
        return f"SELECT DISTINCT {column} AS v FROM products WHERE COALESCE({column}, '') <> '' ORDER BY {column}"

    return {
        "periods": periods_out(),
        "departments": [r["v"] for r in await q(names("department"))],
        "categories": [r["v"] for r in await q(names("category"))],
        "brands": [r["v"] for r in await q(names("brand"))],
        "suppliers": [{"id": r["id"], "name": r["name"]} for r in await q("SELECT id, name FROM suppliers ORDER BY name")],
        "noGroup": NO_GROUP,
    }


async def item_classes(period: Period, product_id: str) -> dict | None:
    """Where one Item sits in the period's ABC (by sales value) and XYZ, for its drill-down."""
    result = await classify(period, "sales", Scope())
    for r in result["items"]:
        if r["productId"] == product_id:
            plan = result["plan"]
            return {**_row_out(r), "bucket": plan.kind, "per": plan.per, "itemsRanked": len(result["items"])}
    return None
