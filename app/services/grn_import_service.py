"""Receiving lines from a supplier's spreadsheet (legacy "Import From Excel" on the Purchase screen).

Reads the file into draft lines and resolves each code to an Item. Nothing is received here: the
lines go back to the Receiving screen for the dock to check against what actually came off the
truck, and are posted only when someone presses Receive.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.schemas.inventory import GRNParsedLine, GRNParseOut
from app.services import catalog_service
from app.services.import_service import cell_str_any, parse_rows, row_error

MAX_LINES = 500

_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%m/%Y")


def _decimal(row: dict, *keys: str, default: str = "0") -> Decimal:
    raw = cell_str_any(row, *keys)
    if raw is None:
        return Decimal(default)
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"{keys[0]} {raw!r} isn't a number") from exc


def _optional_decimal(row: dict, *keys: str) -> Decimal | None:
    return _decimal(row, *keys) if cell_str_any(row, *keys) is not None else None


def _date(row: dict, *keys: str) -> datetime | None:
    raw = cell_str_any(row, *keys)
    if raw is None:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"expiry {raw!r} isn't a date (use 2027-03-31 or 31/03/2027)")


async def parse_lines(filename: str, content: bytes) -> GRNParseOut:
    rows = parse_rows(filename, content)
    lines: list[GRNParsedLine] = []
    errors: list[dict] = []
    if len(rows) > MAX_LINES:
        return GRNParseOut(lines=[], errors=[{"row": 0, "message": f"That file has {len(rows)} lines. Receive at most {MAX_LINES} on one GRN."}])

    for index, row in enumerate(rows, start=2):
        try:
            code = cell_str_any(row, "code", "sku", "barcode", "Barcode", "BARCODE", "AliasName", "ITEM CODE")
            if not code:
                raise ValueError("no Item code")
            found = await catalog_service.lookup_by_code(code)
            if not found:
                raise ValueError(f"no Item with code {code}")
            product, alias = found
            qty = _decimal(row, "qty", "Qty", "QTY", "quantity")
            bonus = _decimal(row, "bonusQty", "bonus", "Bonus", "BONUS")
            price_given = _optional_decimal(row, "unitPrice", "price", "PurPric", "purchasePrice", "PURCHASE PRICE")
            price = price_given if price_given is not None else Decimal("0")
            # A pack barcode in the file (a carton) means the quantity is in packs and the price is per
            # pack. Received stock is counted in units, so both are converted.
            pack = alias.qty if alias and alias.qty and alias.qty > 0 else Decimal("1")
            if qty < 0 or bonus < 0 or price < 0:
                raise ValueError("quantities and prices can't be negative")
            if qty + bonus <= 0:
                raise ValueError("no quantity")
            lines.append(GRNParsedLine(
                row=index, productId=product.id, productName=product.name, productSku=product.sku,
                qty=qty * pack, bonusQty=bonus * pack,
                # No price in the file: the same starting price a typed line gets (average cost, else 80% of
                # the sale price) — never Rs 0, which would drag the Item's average cost down.
                unitPrice=(price / pack).quantize(Decimal("0.01")) if price_given is not None
                else (product.avg_cost if product.avg_cost and product.avg_cost > 0 else product.price * Decimal("0.8")).quantize(Decimal("0.01")),
                discPercent=_decimal(row, "discPercent", "disc%", "Disc%", "discount"),
                flatDisc=_decimal(row, "flatDisc", "FlatDisc", "flat"),
                misc=_decimal(row, "misc", "Misc", "charges"),
                expiry=_date(row, "expiry", "Expiry", "EXPIRY"),
                # No GST in the file means the Item's own rate, as on a typed line — not 0%.
                taxRate=_optional_decimal(row, "taxRate", "gst", "Gst(%)", "GST") if cell_str_any(row, "taxRate", "gst", "Gst(%)", "GST") is not None else product.tax_rate,
                newSalePrice=_optional_decimal(row, "newSalePrice", "salePrice", "SalePrice", "SALE PRICE"),
                newRetailPrice=_optional_decimal(row, "newRetailPrice", "retailPrice", "RetailPrice", "RPP"),
            ))
        except ValueError as exc:
            errors.append({"row": index, "message": row_error(exc)})
    return GRNParseOut(lines=lines, errors=errors)
