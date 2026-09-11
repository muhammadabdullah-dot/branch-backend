"""Reference data — Product/PaymentMethod. Shape matches contracts.md §3.1 / frontend-baseline.md §2.2."""
from tortoise import fields, models


class Product(models.Model):
    id = fields.CharField(max_length=40, pk=True)
    sku = fields.CharField(max_length=40, unique=True)
    name = fields.CharField(max_length=160)
    price = fields.DecimalField(max_digits=12, decimal_places=2)
    tax_rate = fields.DecimalField(max_digits=5, decimal_places=2)
    is_weighed = fields.BooleanField(default=False)
    unit = fields.CharField(max_length=20)
    barcode = fields.CharField(max_length=40, null=True, unique=True)
    pack_unit = fields.CharField(max_length=40, null=True)
    pack_size = fields.IntField(null=True)
    # Weighted-average cost, updated on every GRN receipt (inventory_service.receive_grn).
    # Closes the "no COGS/margin basis anywhere" gap — nothing reads this yet (no margin report
    # exists), but the cost basis is captured from day one instead of being unrecoverable later.
    avg_cost = fields.DecimalField(max_digits=12, decimal_places=4, default=0)

    # Added 2026-09-11 from real legacy export files (old software map.txt §4's Item master).
    # All new columns are nullable additions — tax_rate/unit above stay required at the model
    # level (unchanged) since the real files don't have those columns; the import service fills
    # in a default there rather than the model doing it, to avoid another SQLite ALTER COLUMN.
    rpp = fields.DecimalField(max_digits=12, decimal_places=2, null=True)  # a third, separate price the legacy system tracks
    department = fields.CharField(max_length=80, null=True)
    category = fields.CharField(max_length=80, null=True)
    item_class = fields.CharField(max_length=80, null=True)
    subclass = fields.CharField(max_length=80, null=True)
    manufacturer = fields.CharField(max_length=120, null=True)
    brand = fields.CharField(max_length=120, null=True)
    active = fields.BooleanField(default=True)

    class Meta:
        table = "products"


class ProductAlias(models.Model):
    """The legacy 'AliasName' / alternate-barcode concept — one Product can have several of
    these (real data: 19% of aliased products have 2+, up to 23, almost always different pack
    sizes of the same bulk item). A separate table, not a second column on Product, because the
    relationship is genuinely one-to-many."""

    id = fields.UUIDField(pk=True)
    product: fields.ForeignKeyRelation[Product] = fields.ForeignKeyField(
        "models.Product", related_name="aliases"
    )
    code = fields.CharField(max_length=60, unique=True)
    remarks = fields.CharField(max_length=255, null=True)

    class Meta:
        table = "product_aliases"


class PaymentMethod(models.Model):
    code = fields.CharField(max_length=20, pk=True)
    name = fields.CharField(max_length=60)
    kind = fields.CharField(max_length=20)

    class Meta:
        table = "payment_methods"
