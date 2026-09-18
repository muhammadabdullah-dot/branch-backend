"""Selling in other amounts than the stocked unit: loose pieces and strips of it, or packs and boxes of it.

Stock, price and average cost are always per stocked unit (the Item's `unit`), exactly as before. The counter can also
sell, when the Item says so:

  Smaller (the unit opened and sold loose): an Item with pieces per unit (200 tablets in a box) sells by the piece, and
  by the strip too with pieces per strip. A piece or strip price left blank is the unit price shared out over its pieces,
  to the paisa. The pieces an Item still has loose are whatever is left of the last unit opened: stock is kept as whole
  pieces over pieces per unit (a sale of 5 tablets moves 5/200 of a box, rounded so the running figure is always a whole
  number of tablets), so "3 Box + 45 tablets" is simply 3.225 boxes, and the next unit is opened, without anyone doing
  anything, when the loose pieces run out. Every stock figure, valuation and report that counts units stays right.
  Up to 1,000 pieces to a unit: stock keeps three decimals of a unit.

  Bigger (an Item with a pack unit): `pack_size` stocked units make one pack (a carton of 12 bottles), and packs per box
  packs make a box. A pack or box price left blank is that many units at the unit price.

Legacy Items with pack_size above one and no pack unit are the smaller kind: pack_size was the pieces inside the stocked
unit. `backfill_loose()` copies it into pieces per unit once.
"""
from decimal import ROUND_HALF_UP, Decimal

LOOSE = ("piece", "strip")
TOGETHER = ("pack", "box")
PLACES = Decimal("0.001")
CENT = Decimal("0.01")
MAX_PIECES = 1000


def pieces_per_unit(product) -> int | None:
    n = product.pieces_per_unit
    return n if n and 1 < n <= MAX_PIECES else None


def pieces_in(product, level: str) -> int | None:
    """Pieces in one piece or strip of an Item sold loose, or None when it isn't sold that way."""
    if not pieces_per_unit(product):
        return None
    if level == "piece":
        return 1
    if level == "strip" and product.pieces_per_strip and 1 < product.pieces_per_strip <= product.pieces_per_unit:
        return product.pieces_per_strip
    return None


def units_in(product, level: str) -> int | None:
    """Stocked units in one pack or box, or None when the Item isn't sold that way. A pack needs a pack unit."""
    pack = product.pack_size if product.pack_size and product.pack_size > 1 and (product.pack_unit or "").strip() else None
    if level == "pack":
        return pack
    if level == "box" and product.packs_per_box and product.packs_per_box > 1:
        return (pack or 1) * product.packs_per_box
    return None


def sells_as(product, level: str) -> bool:
    return bool(pieces_in(product, level) if level in LOOSE else units_in(product, level))


def units_for(product, level: str, count: Decimal) -> Decimal:
    """Stocked units in `count` pieces, strips, packs or boxes, to the 0.001 stock keeps."""
    if level in LOOSE:
        return (Decimal(count) * pieces_in(product, level) / pieces_per_unit(product)).quantize(PLACES, rounding=ROUND_HALF_UP)
    return Decimal(count) * units_in(product, level)


def own_price(product, level: str, unit_price: Decimal | None = None) -> Decimal | None:
    """One piece, strip, pack or box at the Item's own price: its own if set, else worked out from the unit price."""
    unit_price = Decimal(product.price) if unit_price is None else Decimal(unit_price)
    if level in LOOSE:
        pieces = pieces_in(product, level)
        if not pieces:
            return None
        own = product.piece_price if level == "piece" else product.strip_price
        return Decimal(own) if own is not None else (unit_price / pieces_per_unit(product) * pieces).quantize(CENT, rounding=ROUND_HALF_UP)
    units = units_in(product, level)
    if not units:
        return None
    own = product.pack_price if level == "pack" else product.box_price
    return Decimal(own) if own is not None else unit_price * units


def wholesale_price(product, level: str, wholesale_unit: Decimal) -> Decimal | None:
    """On a wholesale bill: the wholesale unit price shared out (loose) or multiplied (together)."""
    if level in LOOSE:
        pieces = pieces_in(product, level)
        return (Decimal(wholesale_unit) / pieces_per_unit(product) * pieces).quantize(CENT, rounding=ROUND_HALF_UP) if pieces else None
    units = units_in(product, level)
    return Decimal(wholesale_unit) * units if units else None


def detail(product, level: str | None) -> str | None:
    """What one is, the way a receipt says it: the piece's name, "10" to a strip, "12" to a pack, "10 x 12" for a box."""
    if level == "piece":
        return (product.piece_unit or "").strip() or "pc"
    if level == "strip":
        return str(product.pieces_per_strip)
    pack = product.pack_size if product.pack_size and product.pack_size > 1 and (product.pack_unit or "").strip() else None
    if level == "box" and product.packs_per_box and product.packs_per_box > 1:
        return f"{product.packs_per_box} x {pack}" if pack else str(product.packs_per_box)
    if level == "pack" and pack:
        return str(pack)
    return None


def unit_price(value: Decimal, units: Decimal) -> Decimal:
    """The price of one stocked unit on a line sold another way, to the paisa, for the reports that count units."""
    return (Decimal(value) / Decimal(units)).quantize(CENT, rounding=ROUND_HALF_UP) if units else Decimal("0")


def pieces_of(units: Decimal, per_unit: int) -> int:
    """Whole pieces in a stock figure kept in units (3.225 boxes of 200 is 645 tablets)."""
    return int((Decimal(units) * per_unit).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def units_of(pieces: int, per_unit: int) -> Decimal:
    """The stock figure for a whole number of pieces, as the ledger keeps it."""
    return (Decimal(pieces) / per_unit).quantize(PLACES, rounding=ROUND_HALF_UP)


async def backfill_loose() -> int:
    """Once, and harmless to run again: legacy Items whose pack_size was the pieces inside the stocked unit (no pack unit)
    sell loose by that many pieces. Items already set, or with more than 1,000 pieces to a unit, are left alone."""
    from tortoise import Tortoise

    conn = Tortoise.get_connection("default")
    rows = await conn.execute_query_dict(
        "SELECT COUNT(*) AS n FROM products WHERE pack_size > 1 AND pack_size <= ? AND (pack_unit IS NULL OR TRIM(pack_unit) = '') "
        "AND pieces_per_unit IS NULL",
        [MAX_PIECES],
    )
    count = int(rows[0]["n"]) if rows else 0
    if count:
        await conn.execute_query(
            "UPDATE products SET pieces_per_unit = pack_size WHERE pack_size > 1 AND pack_size <= ? "
            "AND (pack_unit IS NULL OR TRIM(pack_unit) = '') AND pieces_per_unit IS NULL",
            [MAX_PIECES],
        )
    return count
