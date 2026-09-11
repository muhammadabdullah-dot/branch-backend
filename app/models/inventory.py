"""Minimal ledger for I3 — just enough for Sales/Returns to post movements atomically.
I4 builds GRN/Batch/Counts/Adjustments on top of this same table; balances are always
folded from here, never stored (contracts.md §6's first bullet)."""
from tortoise import fields, models


class StockMovement(models.Model):
    id = fields.UUIDField(pk=True)
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="movements"
    )
    location: fields.ForeignKeyRelation["Location"] = fields.ForeignKeyField(
        "models.Location", related_name="movements"
    )
    kind = fields.CharField(max_length=20)
    qty = fields.DecimalField(max_digits=12, decimal_places=3)
    reason = fields.CharField(max_length=20, null=True)
    # Nullable: seed/opening-balance movements have no real actor, mirroring the frontend's own
    # 'system' sentinel (frontend-baseline.md §2.7) rather than inventing a fake user account.
    origin_user: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="stock_movements", null=True
    )
    # Not auto_now_add: seed data needs to backdate opening-balance movements (daysAgo(n)).
    at = fields.DatetimeField()

    class Meta:
        table = "stock_movements"


async def balance_for(product_id: str, location_id: str | None = None):
    from decimal import Decimal

    from tortoise.functions import Sum

    qs = StockMovement.filter(product_id=product_id)
    if location_id:
        qs = qs.filter(location_id=location_id)
    result = await qs.annotate(total=Sum("qty")).values_list("total", flat=True)
    total = result[0] if result else None
    return total if total is not None else Decimal("0")
