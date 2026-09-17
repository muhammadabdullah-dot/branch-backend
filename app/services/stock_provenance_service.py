"""Where this branch's stock came from, and where it went — item by item, for head office.

Every stock movement the branch records falls on one side of a simple sum:

    in:   from the godown · from other branches · from suppliers (GRN) · opening stock and imports ·
          found (adjustments and count gains) · customer returns
    out:  sold · sent to other branches · returned to suppliers · written off (damage, loss, count losses)
    on hand = everything in − everything out

A transfer movement carries its transfer number, so a transfer from head office (`HO`) is told apart
from one another branch sent.

**What today's stock is made of.** Units on the shelf don't carry a label saying where they came from, so
the split of what is on hand assumes the oldest stock sells first: sales, transfers out and write-offs
use up the earliest arrivals, and whatever arrived most recently is what's left. A customer return goes
back as the stock it was sold from. It is an attribution, not a count — but it answers "is this branch
living on godown stock or on its own buying" the way a stock controller would.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from decimal import Decimal

from tortoise import Tortoise

D0 = Decimal("0")

# Sources a unit on the shelf can be traced to.
WAREHOUSE, BRANCHES, WITHIN = "warehouse", "branches", "within"
HEAD_OFFICE_CODE = "HO"

# SQLite's limit on bound parameters is 999; stay well under it.
_IDS_PER_QUERY = 500


def _d(v) -> Decimal:
    return D0 if v is None or v == "" else Decimal(str(v))


def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    text = str(v)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()


def _num(v: Decimal) -> str:
    """Compact decimal for the wire: 12, 0.5 — not 12.000."""
    text = format(v.normalize(), "f")
    return "0" if text in ("-0", "") else text


async def _movements(product_ids: list[str] | None) -> list[dict]:
    conn = Tortoise.get_connection("default")
    sql = """
        SELECT m.product_id AS pid, m.kind, m.qty, m.reason, m.at, l.name AS location
        FROM stock_movements m LEFT JOIN locations l ON l.id = m.location_id
        {where}
        ORDER BY m.product_id, m.at, m.rowid
    """
    if product_ids is None:
        return await conn.execute_query_dict(sql.format(where=""))
    rows: list[dict] = []
    for i in range(0, len(product_ids), _IDS_PER_QUERY):
        chunk = product_ids[i:i + _IDS_PER_QUERY]
        rows += await conn.execute_query_dict(
            sql.format(where=f"WHERE m.product_id IN ({','.join('?' * len(chunk))})"), chunk,
        )
    # Chunks come back separately; each is already in time order per product, and a product never spans two.
    rows.sort(key=lambda r: r["pid"])
    return rows


async def _transfers() -> dict[str, dict]:
    rows = await Tortoise.get_connection("default").execute_query_dict(
        "SELECT number, direction, counterparty_code, from_warehouse FROM transfers WHERE number IS NOT NULL"
    )
    return {r["number"]: r for r in rows}


class _Item:
    __slots__ = ("flows", "lots", "consumed", "deficit", "locations", "last_in", "last_moved")

    def __init__(self) -> None:
        self.flows: dict[str, Decimal] = {}
        self.lots: deque[list] = deque()  # [source, qty], oldest first
        self.consumed: list[list] = []  # [source, qty], most recent last — where returns go back to
        self.deficit = D0  # units gone out that never came in (negative stock)
        self.locations: dict[str, Decimal] = {}
        self.last_in: dict | None = None
        self.last_moved = None

    def flow(self, key: str, qty: Decimal) -> None:
        self.flows[key] = self.flows.get(key, D0) + qty

    def arrive(self, source: str, qty: Decimal) -> None:
        cover = min(self.deficit, qty)
        self.deficit -= cover
        qty -= cover
        if qty > 0:
            if self.lots and self.lots[-1][0] == source:
                self.lots[-1][1] += qty
            else:
                self.lots.append([source, qty])

    def leave(self, qty: Decimal) -> None:
        while qty > 0 and self.lots:
            lot = self.lots[0]
            take = min(lot[1], qty)
            lot[1] -= take
            qty -= take
            if self.consumed and self.consumed[-1][0] == lot[0]:
                self.consumed[-1][1] += take
            else:
                self.consumed.append([lot[0], take])
            if lot[1] <= 0:
                self.lots.popleft()
        self.deficit += qty

    def come_back(self, qty: Decimal) -> None:
        """A customer return: back on the shelf as the stock it was sold from, ahead of newer arrivals."""
        while qty > 0 and self.consumed:
            last = self.consumed[-1]
            take = min(last[1], qty)
            last[1] -= take
            qty -= take
            if last[1] <= 0:
                self.consumed.pop()
            cover = min(self.deficit, take)
            self.deficit -= cover
            if take - cover > 0:
                self.lots.appendleft([last[0], take - cover])
        if qty > 0:
            self.arrive(WITHIN, qty)

    def out(self) -> dict:
        origin: dict[str, Decimal] = {}
        for source, qty in self.lots:
            if qty > 0:
                origin[source] = origin.get(source, D0) + qty
        return {
            "flows": {k: _num(v) for k, v in self.flows.items() if v != 0},
            "origin": {k: _num(v) for k, v in origin.items() if v != 0},
            "locations": [{"name": n, "qty": _num(q)} for n, q in sorted(self.locations.items()) if q != 0],
            "lastIn": self.last_in,
            "lastMovedAt": _iso(self.last_moved),
        }


async def detail(product_ids: list[str] | None = None) -> dict[str, dict]:
    """Flows, what on-hand stock is made of, stock per location and the latest arrival — per product id.
    Products that never moved are absent."""
    transfers = await _transfers()
    items: dict[str, _Item] = {}
    for m in await _movements(product_ids):
        item = items.get(m["pid"])
        if item is None:
            item = items[m["pid"]] = _Item()
        qty = _d(m["qty"])
        kind, reason = m["kind"], m["reason"]
        name = m["location"] or "-"
        item.locations[name] = item.locations.get(name, D0) + qty
        item.last_moved = m["at"]

        if kind == "transfer-in" and qty > 0:
            t = transfers.get(reason or "")
            code = (t or {}).get("counterparty_code")
            from_branch = bool(code) and code != HEAD_OFFICE_CODE
            item.flow("fromBranches" if from_branch else "fromWarehouse", qty)
            item.arrive(BRANCHES if from_branch else WAREHOUSE, qty)
            item.last_in = {
                "kind": "branch" if from_branch else "warehouse", "ref": reason, "qty": _num(qty), "at": _iso(m["at"]),
                "from": (t or {}).get("from_warehouse") or ("Central Godown" if not from_branch else code), "code": code or HEAD_OFFICE_CODE,
            }
        elif kind == "receive" and qty > 0:
            supplier = bool(reason)
            item.flow("fromSuppliers" if supplier else "opening", qty)
            item.arrive(WITHIN, qty)
            if supplier:
                item.last_in = {"kind": "supplier", "ref": reason, "qty": _num(qty), "at": _iso(m["at"]), "from": None, "code": None}
        elif kind in ("return", "sell") and qty > 0:
            item.flow("customerReturns", qty)
            item.come_back(qty)
        elif kind == "sell" and qty < 0:
            item.flow("sold", -qty)
            item.leave(-qty)
        elif kind == "transfer-out" and qty < 0:
            item.flow("toBranches", -qty)
            item.leave(-qty)
        elif kind == "purchase-return" and qty < 0:
            item.flow("toSuppliers", -qty)
            item.leave(-qty)
        elif qty > 0:  # adjustments and count corrections that found stock, and anything newer
            item.flow("found", qty)
            item.arrive(WITHIN, qty)
        elif qty < 0:
            item.flow("writtenOff", -qty)
            item.leave(-qty)
    return {pid: item.out() for pid, item in items.items()}
