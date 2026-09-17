"""Minimal ledger for I3 — just enough for Sales/Returns to post movements atomically.
I4 builds GRN/Batch/Counts/Adjustments on top of this same table; balances are always
folded from here, never stored (contracts.md §6's first bullet)."""
from decimal import Decimal

from tortoise import fields, models
from tortoise.functions import Sum
from tortoise.signals import pre_save


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
    # What one unit of this movement was worth at cost when it happened. Null on older movements.
    unit_cost = fields.DecimalField(max_digits=12, decimal_places=4, null=True)

    class Meta:
        table = "stock_movements"
        # Stock on hand is a fold over one item's movements, and the snapshot folds every item every
        # couple of hours: without this it is a full scan of the whole ledger each time.
        indexes = (("product", "at"),)


async def balance_for(product_id: str, location_id: str | None = None):
    from decimal import Decimal

    from tortoise.functions import Sum

    qs = StockMovement.filter(product_id=product_id)
    if location_id:
        qs = qs.filter(location_id=location_id)
    result = await qs.annotate(total=Sum("qty")).values_list("total", flat=True)
    total = result[0] if result else None
    return total if total is not None else Decimal("0")


class StockBelowZero(Exception):
    """A movement that would leave a location holding less than nothing. Shown to the person as it is (409)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@pre_save(StockMovement)
async def _never_below_zero(sender, instance: StockMovement, using_db, update_fields) -> None:
    """The last line: whatever path wrote this movement, no location may hold less than nothing. Services check first
    with their own words (services/stock_guard.py); this catches anything that didn't."""
    if instance._saved_in_db or instance.qty is None or Decimal(str(instance.qty)) >= 0:
        return
    qs = StockMovement.filter(product_id=instance.product_id, location_id=instance.location_id)
    if using_db is not None:
        qs = qs.using_db(using_db)
    rows = await qs.annotate(total=Sum("qty")).values_list("total", flat=True)
    held = Decimal(str(rows[0])) if rows and rows[0] is not None else Decimal("0")
    if held + Decimal(str(instance.qty)) < Decimal("-0.0005"):
        from app.models.catalog import Product
        from app.models.location import Location

        product = await Product.get_or_none(id=instance.product_id)
        location = await Location.get_or_none(id=instance.location_id)
        name = product.name if product else "This Item"
        where = location.name if location else "This location"
        have = format(held.quantize(Decimal("0.001")).normalize(), "f")
        need = format((-Decimal(str(instance.qty))).quantize(Decimal("0.001")).normalize(), "f")
        raise StockBelowZero(f"{where} holds {have} of {name}, not enough to take {need}. Stock can't go below zero, so nothing was saved.")
