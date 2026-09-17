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

    # Added 2026-09-14 from the legacy Item form.
    # The Item's own sale discount: a percentage and/or a flat amount per unit, applied on the bill.
    disc_percent = fields.DecimalField(max_digits=5, decimal_places=2, default=0)
    disc_flat = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Legacy LockDisc: no bill-level discount may reduce this Item's price.
    lock_disc = fields.BooleanField(default=False)
    variant = fields.CharField(max_length=60, null=True)
    # Legacy IMP/LOCAL: "local" or "imported".
    origin = fields.CharField(max_length=10, null=True)
    remarks = fields.CharField(max_length=255, null=True)
    picture = fields.CharField(max_length=160, null=True)
    # Legacy Child And Parent: this Item is a smaller pack of `parent`, and `parent_qty` of this
    # Item make one parent (a strip of 10 inside a box, a bottle inside a carton of 12).
    parent: fields.ForeignKeyNullableRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="children", null=True, on_delete=fields.SET_NULL
    )
    parent_qty = fields.DecimalField(max_digits=12, decimal_places=3, null=True)

    # Added 2026-09-17. The Item's own wholesale price. Blank: wholesale bills take the branch's wholesale discount
    # off the sale price (Lists and Settings > Receipt and Vouchers).
    wholesale_price = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    # Low stock at this branch means fewer than this left. Blank: the branch's usual low stock level.
    reorder_level = fields.DecimalField(max_digits=12, decimal_places=3, null=True)

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
    # Legacy Alternate Barcode grid: how many units one scan of this code is (a carton barcode = 12),
    # and the discount that pack sells at.
    qty = fields.DecimalField(max_digits=12, decimal_places=3, default=1)
    disc_percent = fields.DecimalField(max_digits=5, decimal_places=2, default=0)
    disc_flat = fields.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        table = "product_aliases"


class ProductSupplier(models.Model):
    """Who supplies this Item, in order of preference — the legacy Item form's Supplier grid."""

    id = fields.UUIDField(pk=True)
    product: fields.ForeignKeyRelation[Product] = fields.ForeignKeyField(
        "models.Product", related_name="supplier_links", on_delete=fields.CASCADE
    )
    supplier: fields.ForeignKeyRelation["Supplier"] = fields.ForeignKeyField(
        "models.Supplier", related_name="product_links", on_delete=fields.CASCADE
    )
    priority = fields.IntField(default=1)

    class Meta:
        table = "product_suppliers"
        unique_together = (("product", "supplier"),)


class ProductPriceChange(models.Model):
    """Every change to an Item's sale or retail price, and where it came from. Labels reads this to
    reprint shelf tags for exactly the Items whose price moved (legacy "Check New Price List")."""

    id = fields.UUIDField(pk=True)
    product: fields.ForeignKeyRelation[Product] = fields.ForeignKeyField(
        "models.Product", related_name="price_changes", on_delete=fields.CASCADE
    )
    old_price = fields.DecimalField(max_digits=12, decimal_places=2)
    new_price = fields.DecimalField(max_digits=12, decimal_places=2)
    old_rpp = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    new_rpp = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    # "form", "import" or "receiving"
    source = fields.CharField(max_length=20)
    changed_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="price_changes", null=True, on_delete=fields.SET_NULL
    )
    at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "product_price_changes"


class PaymentMethod(models.Model):
    code = fields.CharField(max_length=20, pk=True)
    name = fields.CharField(max_length=60)
    kind = fields.CharField(max_length=20)
    # A branch without a card machine, or not taking JazzCash, switches that method off: Billing, Returns and
    # Gift Vouchers stop offering it and the server refuses it. Cash can't be switched off.
    active = fields.BooleanField(default=True)
    # The order the methods are listed in on the Payment Methods screen.
    sort_order = fields.IntField(default=0)

    class Meta:
        table = "payment_methods"
