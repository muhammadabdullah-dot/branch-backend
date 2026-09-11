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
    at = fields.DatetimeField(auto_now_add=True)

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
    expiry = fields.DatetimeField(null=True)
    tax_rate = fields.DecimalField(max_digits=5, decimal_places=2, default=0)

    class Meta:
        table = "grn_lines"
