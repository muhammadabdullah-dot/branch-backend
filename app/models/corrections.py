from tortoise import fields, models


class PhysicalCount(models.Model):
    id = fields.UUIDField(pk=True)
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="counts"
    )
    location: fields.ForeignKeyRelation["Location"] = fields.ForeignKeyField(
        "models.Location", related_name="counts"
    )
    system_qty = fields.DecimalField(max_digits=12, decimal_places=3)
    counted_qty = fields.DecimalField(max_digits=12, decimal_places=3)
    status = fields.CharField(max_length=10, default="pending")
    counted_by: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="counts_submitted"
    )
    approved_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="counts_approved", null=True
    )
    at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "physical_counts"


class Adjustment(models.Model):
    id = fields.UUIDField(pk=True)
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="adjustments"
    )
    location: fields.ForeignKeyRelation["Location"] = fields.ForeignKeyField(
        "models.Location", related_name="adjustments"
    )
    reason = fields.CharField(max_length=20)
    magnitude = fields.DecimalField(max_digits=12, decimal_places=3)
    notes = fields.TextField(null=True)
    status = fields.CharField(max_length=10, default="pending")
    submitted_by: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="adjustments_submitted"
    )
    decided_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="adjustments_decided", null=True
    )
    at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "adjustments"
