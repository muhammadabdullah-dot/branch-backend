from tortoise import fields, models


class Batch(models.Model):
    id = fields.UUIDField(pk=True)
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="batches"
    )
    lot_number = fields.CharField(max_length=60, null=True)
    expiry = fields.DatetimeField(null=True)
    received_qty = fields.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        table = "batches"


class GRN(models.Model):
    id = fields.UUIDField(pk=True)
    grn_number = fields.CharField(max_length=20, unique=True)
    supplier: fields.ForeignKeyRelation["Supplier"] = fields.ForeignKeyField(
        "models.Supplier", related_name="grns"
    )
    party_inv_no = fields.CharField(max_length=60, null=True)
    location: fields.ForeignKeyRelation["Location"] = fields.ForeignKeyField(
        "models.Location", related_name="grns"
    )
    gst_mode = fields.CharField(max_length=20, default="normal")
    advance_tax = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    approved = fields.BooleanField(default=True)
    received_by: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="grns_received"
    )
    # The order this delivery was received against, if any.
    purchase_order: fields.ForeignKeyNullableRelation["PurchaseOrder"] = fields.ForeignKeyField(
        "models.PurchaseOrder", related_name="grns", null=True, on_delete=fields.SET_NULL
    )
    at = fields.DatetimeField(auto_now_add=True)
    # The bill: goods at price, discounts, GST, and what's owed to the supplier (net + GST + advance tax).
    # Null on GRNs from before these were kept — they're worked out from the lines.
    gross_total = fields.DecimalField(max_digits=14, decimal_places=2, null=True)
    disc_total = fields.DecimalField(max_digits=14, decimal_places=2, null=True)
    tax_total = fields.DecimalField(max_digits=14, decimal_places=2, null=True)
    net_total = fields.DecimalField(max_digits=14, decimal_places=2, null=True)
    due_date = fields.DateField(null=True)

    class Meta:
        table = "grns"


class GRNLine(models.Model):
    id = fields.UUIDField(pk=True)
    grn: fields.ForeignKeyRelation[GRN] = fields.ForeignKeyField("models.GRN", related_name="lines")
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="grn_lines"
    )
    qty = fields.DecimalField(max_digits=12, decimal_places=3)
    bonus_qty = fields.DecimalField(max_digits=12, decimal_places=3, default=0)
    unit_price = fields.DecimalField(max_digits=12, decimal_places=2)
    disc_percent = fields.DecimalField(max_digits=5, decimal_places=2, default=0)
    # Legacy DiscFlatEN / MiscEN: a rupee discount off the whole line, and extra charges on it
    # (freight, loading). Both move the line's cost, so both move the Item's average cost.
    flat_disc = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    misc = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    expiry = fields.DatetimeField(null=True)
    tax_rate = fields.DecimalField(max_digits=5, decimal_places=2, default=0)
    # Sale / retail price set while receiving, when the delivery came with a new price. Null = unchanged.
    new_sale_price = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    new_retail_price = fields.DecimalField(max_digits=12, decimal_places=2, null=True)

    class Meta:
        table = "grn_lines"
