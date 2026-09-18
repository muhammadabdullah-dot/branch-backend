"""Items in stock whose own price is at or below what they cost, and Items with no cost recorded.

No discount can sell below cost (services/sale_rules.py), but an Item whose own sale price, or its own wholesale price, is
already at or below its average cost with tax still sells at that price: it just takes no discount at all. This is the
list management fixes those prices from. Price and cost are compared with tax on both sides, as Billing does.

An Item with no average cost recorded (never received, or imported without a purchase price) has a floor of nothing,
so every discount is open to it; it is listed on its own so a cost can be put on it.
"""
from datetime import datetime, timezone
from decimal import Decimal

from tortoise import Tortoise

from app.services.sale_rules import TOLERANCE, with_tax

ZERO = Decimal("0")


def _d(value) -> Decimal:
    return ZERO if value is None or value == "" else Decimal(str(value))


def _instant(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _in_stock() -> dict[str, Decimal]:
    """What the branch holds of each Item across its switched-on locations, counting only locations that hold some."""
    rows = await Tortoise.get_connection("default").execute_query_dict(
        "SELECT m.product_id AS pid, SUM(CAST(m.qty AS REAL)) AS total FROM stock_movements m "
        "JOIN locations l ON l.id = m.location_id WHERE l.active = 1 GROUP BY m.product_id, m.location_id"
    )
    held: dict[str, Decimal] = {}
    for row in rows:
        total = _d(row["total"]).quantize(Decimal("0.001"))
        if total > 0:
            held[str(row["pid"])] = held.get(str(row["pid"]), ZERO) + total
    return held


async def priced_below_cost() -> dict:
    """{belowCost: [...], noCost: [...]}, each Item with its prices, cost with tax, stock and last delivery."""
    held = await _in_stock()
    if not held:
        return {"belowCost": [], "noCost": []}
    conn = Tortoise.get_connection("default")
    ids = list(held)
    products: list[dict] = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        products += await conn.execute_query_dict(
            "SELECT id, sku, name, department, unit, price, wholesale_price, avg_cost, tax_rate, pack_size, pack_unit, packs_per_box, "
            "pack_price, box_price, pieces_per_unit, pieces_per_strip, piece_price, strip_price FROM products "
            f"WHERE id IN ({','.join('?' * len(chunk))})",
            chunk,
        )
    below: list[dict] = []
    no_cost: list[dict] = []
    for p in products:
        cost, rate = _d(p["avg_cost"]), _d(p["tax_rate"])
        row = {
            "productId": str(p["id"]), "sku": p["sku"], "name": p["name"], "department": p["department"], "unit": p["unit"],
            "price": _d(p["price"]), "wholesalePrice": _d(p["wholesale_price"]) if p["wholesale_price"] is not None else None,
            "costWithTax": with_tax(cost, rate), "priceWithTax": with_tax(_d(p["price"]), rate), "onHand": held[str(p["id"])],
        }
        if cost <= 0:
            no_cost.append(row)
            continue
        # Each of its own prices, as the price of one stocked unit: the sale price, its own wholesale price, its own pack
        # and box prices (several units), and its own piece and strip prices (a unit sold loose).
        pack = int(p["pack_size"]) if p["pack_size"] and int(p["pack_size"]) > 1 and (p["pack_unit"] or "").strip() else None
        box = (pack or 1) * int(p["packs_per_box"]) if p["packs_per_box"] and int(p["packs_per_box"]) > 1 else None
        per_unit = int(p["pieces_per_unit"]) if p["pieces_per_unit"] and 1 < int(p["pieces_per_unit"]) <= 1000 else None
        strip = int(p["pieces_per_strip"]) if per_unit and p["pieces_per_strip"] and 1 < int(p["pieces_per_strip"]) <= per_unit else None
        own = [("Sale price", _d(p["price"]))]
        if p["wholesale_price"] is not None:
            own.append(("Wholesale price", _d(p["wholesale_price"])))
        if pack and p["pack_price"] is not None:
            own.append(("Pack price", _d(p["pack_price"]) / pack))
        if box and p["box_price"] is not None:
            own.append(("Box price", _d(p["box_price"]) / box))
        if per_unit and p["piece_price"] is not None:
            own.append(("Piece price", _d(p["piece_price"]) * per_unit))
        if strip and p["strip_price"] is not None:
            own.append(("Strip price", _d(p["strip_price"]) * per_unit / strip))
        low = [(name, per) for name, per in own if per <= cost + TOLERANCE]
        if not low:
            continue
        row["which"] = " and ".join(name for name, _ in low)
        row["gap"] = with_tax(cost, rate) - with_tax(min(per for _, per in low), rate)
        below.append(row)

    # The last delivery of each listed Item: when, from whom, on which GRN and at what price.
    listed = [r["productId"] for r in below + no_cost]
    last: dict[str, dict] = {}
    for i in range(0, len(listed), 500):
        chunk = listed[i:i + 500]
        rows = await conn.execute_query_dict(
            "SELECT gl.product_id AS pid, g.at AS at, g.grn_number AS grn, s.name AS supplier, gl.unit_price AS unit_price "
            "FROM grn_lines gl JOIN grns g ON g.id = gl.grn_id LEFT JOIN suppliers s ON s.id = g.supplier_id "
            f"WHERE gl.product_id IN ({','.join('?' * len(chunk))}) ORDER BY g.at",
            chunk,
        )
        for r in rows:
            last[str(r["pid"])] = {"at": _instant(r["at"]), "grnNumber": r["grn"], "supplier": r["supplier"], "unitPrice": _d(r["unit_price"])}
    for row in below + no_cost:
        row["lastReceived"] = last.get(row["productId"])
    below.sort(key=lambda r: r["gap"] * r["onHand"], reverse=True)
    no_cost.sort(key=lambda r: r["onHand"] * r["price"], reverse=True)
    return {"belowCost": below, "noCost": no_cost}
