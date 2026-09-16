from tortoise import fields, models


class PurchaseOrder(models.Model):
    """What the branch has asked a supplier for. Receiving fills a GRN from an approved one and ticks
    off what arrived, so a short delivery shows as a PO still waiting rather than being forgotten.

    Lifecycle: draft → approved → partially-received → received. A draft can be edited or cancelled;
    an approved order with nothing received can be cancelled; one partly received can be closed,
    which gives up on the rest."""

    id = fields.UUIDField(pk=True)
    po_number = fields.CharField(max_length=20, unique=True)
    supplier: fields.ForeignKeyRelation["Supplier"] = fields.ForeignKeyField("models.Supplier", related_name="purchase_orders")
    location: fields.ForeignKeyRelation["Location"] = fields.ForeignKeyField("models.Location", related_name="purchase_orders")
    status = fields.CharField(max_length=20, default="draft")
    expected_at = fields.DatetimeField(null=True)
    notes = fields.CharField(max_length=255, null=True)
    created_by: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField("models.User", related_name="purchase_orders_created")
    approved_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="purchase_orders_approved", null=True
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    approved_at = fields.DatetimeField(null=True)
    closed_at = fields.DatetimeField(null=True)

    class Meta:
        table = "purchase_orders"


class PurchaseOrderLine(models.Model):
    id = fields.UUIDField(pk=True)
    purchase_order: fields.ForeignKeyRelation[PurchaseOrder] = fields.ForeignKeyField(
        "models.PurchaseOrder", related_name="lines", on_delete=fields.CASCADE
    )
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField("models.Product", related_name="purchase_order_lines")
    qty = fields.DecimalField(max_digits=12, decimal_places=3)
    unit_price = fields.DecimalField(max_digits=12, decimal_places=2)
    disc_percent = fields.DecimalField(max_digits=5, decimal_places=2, default=0)
    # Paid-for units received against this line so far. Bonus units don't count toward the order.
    received_qty = fields.DecimalField(max_digits=12, decimal_places=3, default=0)
    position = fields.IntField(default=0)

    class Meta:
        table = "purchase_order_lines"
