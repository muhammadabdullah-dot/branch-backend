"""The legacy User Wise Sales report, grouped any of the ways Multi Soft offered that apply here.

Every figure is folded from the bills themselves. Bill-level amounts — discount, FARE, GST — are
shared across a bill's lines by their value, the same way the sale split its flat discount, so an Item
or brand grouping adds up to exactly the same totals as a user or date grouping. Payment columns (credit,
vouchers & cards, cash) only make sense per bill, so they're given for user, date and customer groupings
and left blank for Item-level ones.

Refunds processed on the Returns screen count on the day and against the salesperson who processed them.

Each sale line records its own discount and the Item's average cost at the moment it was sold; those are
used as they are. Sales from before lines recorded them fall back to sharing the bill discount by value and
to the Item's current average cost. Gross profit is sales after discounts and returns, before GST and FARE,
less that cost.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.core.pk_time import PKT
from app.models import GRN, GRNLine, Party, Product, ProductSupplier, ReturnLine, ReturnRecord, SaleLine, SaleRecord, SaleTender, User
from app.services.inventory_service import in_window

ZERO = Decimal("0")

BILL_GROUPS = ("user", "date", "customer")
LINE_GROUPS = ("item", "brand", "category", "department", "itemClass", "manufacturer", "supplier")
GROUPS = BILL_GROUPS + LINE_GROUPS

# Paid some way other than cash or credit: card, gift voucher, Easypaisa / JazzCash, bank transfer, loyalty points.
CARD_LIKE = ("CARD", "VOUCHER", "EASYPAISA", "JAZZCASH", "BANK", "POINTS")


@dataclass
class Row:
    key: str
    label: str
    invoices: set = field(default_factory=set)
    qty: Decimal = ZERO
    gross: Decimal = ZERO
    discount: Decimal = ZERO
    misc: Decimal = ZERO
    gst: Decimal = ZERO
    returns: Decimal = ZERO
    credit: Decimal = ZERO
    vouchers_cards: Decimal = ZERO
    cash: Decimal = ZERO
    cost: Decimal = ZERO

    @property
    def revenue(self) -> Decimal:
        """Sales after discounts and returns, before GST and FARE."""
        return self.gross - self.returns - self.discount

    @property
    def net(self) -> Decimal:
        return self.revenue + self.misc + self.gst

    @property
    def profit(self) -> Decimal:
        return self.revenue - self.cost


def _shop_day(at: datetime, day_start_hour: int) -> str:
    local = at.astimezone(PKT) - timedelta(hours=day_start_hour)
    return local.strftime("%Y-%m-%d")


async def _supplier_by_product(product_ids: set[str]) -> dict[str, str]:
    """An Item's first-choice supplier from the Item form, else whoever delivered it last."""
    names: dict[str, str] = {}
    for link in await ProductSupplier.filter(product_id__in=list(product_ids)).order_by("priority").prefetch_related("supplier"):
        names.setdefault(str(link.product_id), link.supplier.name)
    missing = [pid for pid in product_ids if pid not in names]
    if missing:
        lines = await GRNLine.filter(product_id__in=missing).order_by("-grn__at").values("product_id", "grn__supplier__name")
        for line in lines:
            names.setdefault(str(line["product_id"]), line["grn__supplier__name"])
    return names


async def summary(group_by: str, from_at: datetime | None, to_at: datetime | None, day_start_hour: int = 0) -> dict:
    if group_by not in GROUPS:
        group_by = "user"
    bill_level = group_by in BILL_GROUPS

    sales = await in_window(SaleRecord.all(), "at", from_at, to_at).values(
        "id", "invoice_number", "at", "cashier_id", "party_id", "gross", "disc_total", "fare", "gst", "cash_back",
    )
    sale_ids = [s["id"] for s in sales]
    lines = await SaleLine.filter(sale_id__in=sale_ids).values(
        "sale_id", "product_id", "qty", "unit_price", "is_return", "disc_amount", "unit_cost",
    ) if sale_ids else []
    tenders = await SaleTender.filter(sale_id__in=sale_ids).values("sale_id", "code", "amount") if sale_ids else []
    refunds = await in_window(ReturnRecord.all(), "at", from_at, to_at).values(
        "id", "at", "cashier_id", "refund_total", "refund_method", "against__party_id",
    )
    refund_lines = await ReturnLine.filter(return_record_id__in=[r["id"] for r in refunds]).values(
        "return_record_id", "product_id", "qty", "unit_price",
    ) if refunds else []

    product_ids = {str(l["product_id"]) for l in lines} | {str(l["product_id"]) for l in refund_lines}
    products = {p["id"]: p for p in await Product.filter(id__in=list(product_ids)).values(
        "id", "sku", "name", "brand", "category", "department", "item_class", "manufacturer", "avg_cost",
    )} if product_ids else {}
    user_names = dict(await User.all().values_list("id", "name"))
    party_ids = {s["party_id"] for s in sales} | {r["against__party_id"] for r in refunds}
    parties = {p["id"]: p for p in await Party.filter(id__in=list(party_ids)).values("id", "code", "name", "is_walk_in")} if party_ids else {}
    suppliers = await _supplier_by_product(product_ids) if group_by == "supplier" else {}

    def product_key(pid: str) -> tuple[str, str]:
        p = products.get(pid) or {}
        if group_by == "item":
            return pid, f"{p.get('name', pid)} ({p.get('sku', '')})"
        if group_by == "supplier":
            name = suppliers.get(pid)
            return (name or "~none"), (name or "No supplier on record")
        column = {"itemClass": "item_class"}.get(group_by, group_by)
        value = p.get(column)
        return (value or "~none"), (value or f"No {'class' if group_by == 'itemClass' else group_by}")

    def bill_key(at: datetime, cashier_id, party_id) -> tuple[str, str]:
        if group_by == "user":
            return str(cashier_id), user_names.get(cashier_id, "Unknown user")
        if group_by == "date":
            day = _shop_day(at, day_start_hour)
            return day, day
        party = parties.get(party_id) or {}
        if party.get("is_walk_in"):
            return "~walk-in", "Walk-in customers"
        return str(party_id), f"{party.get('name', 'Unknown')} ({party.get('code', '')})"

    rows: dict[str, Row] = {}

    def row_for(key: str, label: str) -> Row:
        if key not in rows:
            rows[key] = Row(key=key, label=label)
        return rows[key]

    lines_by_sale: dict = defaultdict(list)
    for line in lines:
        lines_by_sale[line["sale_id"]].append(line)
    tenders_by_sale: dict = defaultdict(list)
    for tender in tenders:
        tenders_by_sale[tender["sale_id"]].append(tender)

    for sale in sales:
        sale_lines = lines_by_sale.get(sale["id"], [])
        values = [(-1 if l["is_return"] else 1) * l["qty"] * l["unit_price"] for l in sale_lines]
        bill_gross = sum(values, ZERO)
        shares = [(v / bill_gross) if bill_gross else ZERO for v in values]
        stored = bool(sale_lines) and all(l["disc_amount"] is not None for l in sale_lines)
        line_discounts = [l["disc_amount"] if stored else sale["disc_total"] * s for l, s in zip(sale_lines, shares)]
        revenues = [v - d for v, d in zip(values, line_discounts)]
        bill_revenue = sum(revenues, ZERO)

        if bill_level:
            row = row_for(*bill_key(sale["at"], sale["cashier_id"], sale["party_id"]))
            row.invoices.add(sale["id"])
            row.discount += sale["disc_total"]
            row.misc += sale["fare"]
            row.gst += sale["gst"]
            for tender in tenders_by_sale.get(sale["id"], []):
                if tender["code"] == "CREDIT":
                    row.credit += tender["amount"]
                elif tender["code"] in CARD_LIKE:
                    row.vouchers_cards += tender["amount"]
                elif tender["code"] == "CASH":
                    row.cash += tender["amount"]
            row.cash -= sale["cash_back"]

        for line, value, share, revenue, line_discount in zip(sale_lines, values, shares, revenues, line_discounts):
            pid = str(line["product_id"])
            cost = line["unit_cost"] if line["unit_cost"] is not None else ((products.get(pid) or {}).get("avg_cost") or ZERO)
            target = rows[bill_key(sale["at"], sale["cashier_id"], sale["party_id"])[0]] if bill_level else row_for(*product_key(pid))
            if line["is_return"]:
                target.returns += -value
                target.qty -= line["qty"]
                target.cost -= line["qty"] * cost
            else:
                target.gross += value
                target.qty += line["qty"]
                target.cost += line["qty"] * cost
            if not bill_level:
                target.invoices.add(sale["id"])
                target.discount += line_discount
                target.misc += sale["fare"] * share
                target.gst += (sale["gst"] * (revenue / bill_revenue)) if bill_revenue else ZERO

    refund_lines_by_record: dict = defaultdict(list)
    for line in refund_lines:
        refund_lines_by_record[line["return_record_id"]].append(line)
    for refund in refunds:
        record_lines = refund_lines_by_record.get(refund["id"], [])
        if bill_level:
            row = row_for(*bill_key(refund["at"], refund["cashier_id"], refund["against__party_id"]))
            row.returns += refund["refund_total"]
            if refund["refund_method"] == "CASH":
                row.cash -= refund["refund_total"]
            else:
                row.vouchers_cards -= refund["refund_total"]
        for line in record_lines:
            pid = str(line["product_id"])
            cost = (products.get(pid) or {}).get("avg_cost") or ZERO
            target = rows[bill_key(refund["at"], refund["cashier_id"], refund["against__party_id"])[0]] if bill_level else row_for(*product_key(pid))
            if not bill_level:
                target.returns += line["qty"] * line["unit_price"]
            target.qty -= line["qty"]
            target.cost -= line["qty"] * cost

    def out(row: Row) -> dict:
        revenue = row.revenue
        return {
            "key": row.key, "label": row.label, "invoices": len(row.invoices), "qty": _q(row.qty),
            "gross": _m(row.gross), "discount": _m(row.discount), "misc": _m(row.misc), "gst": _m(row.gst),
            "returns": _m(row.returns), "net": _m(row.net),
            "credit": _m(row.credit) if bill_level else None,
            "vouchersCards": _m(row.vouchers_cards) if bill_level else None,
            "cash": _m(row.cash) if bill_level else None,
            "cost": _m(row.cost), "profit": _m(row.profit),
            "profitPercent": float((row.profit / revenue * 100).quantize(Decimal("0.1"))) if revenue else None,
        }

    ordered = sorted(rows.values(), key=lambda r: (r.key if group_by == "date" else "", -r.net if group_by != "date" else 0))
    total = Row(key="total", label="Total")
    for row in rows.values():
        total.invoices |= row.invoices
        for attr in ("qty", "gross", "discount", "misc", "gst", "returns", "credit", "vouchers_cards", "cash", "cost"):
            setattr(total, attr, getattr(total, attr) + getattr(row, attr))
    return {
        "groupBy": group_by,
        "billLevel": bill_level,
        "rows": [out(r) for r in ordered],
        "totals": out(total),
        "costBasis": "cost-at-sale-else-current-average",
    }


def _m(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _q(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.001")).normalize(), "f")
