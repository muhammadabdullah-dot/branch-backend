"""Purchase returns — stock going back to a supplier (the inverse of a GRN). Posted immediately
like a GRN (no approval workflow), since it mirrors receiving's own immediacy rather than
Counts/Adjustments' pending-then-decided flow."""
from tortoise import fields, models


class PurchaseReturn(models.Model):
    id = fields.UUIDField(pk=True)
    return_number = fields.CharField(max_length=20, unique=True)
    supplier: fields.ForeignKeyRelation["Supplier"] = fields.ForeignKeyField(
        "models.Supplier", related_name="purchase_returns"
    )
    location: fields.ForeignKeyRelation["Location"] = fields.ForeignKeyField(
        "models.Location", related_name="purchase_returns"
    )
    # Nullable: a return isn't always traceable to one specific GRN (e.g. old stock returned
    # long after receiving, or never cleanly tied to a single incoming shipment).
    grn: fields.ForeignKeyNullableRelation["GRN"] = fields.ForeignKeyField(
        "models.GRN", related_name="purchase_returns", null=True
    )
    reason = fields.CharField(max_length=20)
    notes = fields.TextField(null=True)
    submitted_by: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="purchase_returns_submitted"
    )
    at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "purchase_returns"


class PurchaseReturnLine(models.Model):
    id = fields.UUIDField(pk=True)
    purchase_return: fields.ForeignKeyRelation[PurchaseReturn] = fields.ForeignKeyField(
        "models.PurchaseReturn", related_name="lines"
    )
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="purchase_return_lines"
    )
    qty = fields.DecimalField(max_digits=12, decimal_places=3)
    unit_price = fields.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        table = "purchase_return_lines"
