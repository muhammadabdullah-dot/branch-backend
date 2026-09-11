from tortoise import fields, models


class TillSession(models.Model):
    id = fields.UUIDField(pk=True)
    session_number = fields.CharField(max_length=20, unique=True)
    opened_at = fields.DatetimeField()
    closed_at = fields.DatetimeField(null=True)
    opened_by: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="opened_tills"
    )
    opening_float = fields.DecimalField(max_digits=12, decimal_places=2)
    opening_denominations = fields.JSONField()
    opening_notes = fields.TextField(null=True)
    status = fields.CharField(max_length=10, default="open")
    net_cash = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    counted_cash = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    variance = fields.DecimalField(max_digits=12, decimal_places=2, null=True)

    class Meta:
        table = "till_sessions"


class CashMovement(models.Model):
    id = fields.UUIDField(pk=True)
    till_session: fields.ForeignKeyRelation[TillSession] = fields.ForeignKeyField(
        "models.TillSession", related_name="movements"
    )
    kind = fields.CharField(max_length=10)
    amount = fields.DecimalField(max_digits=12, decimal_places=2)
    denominations = fields.JSONField()
    notes = fields.TextField(null=True)
    at = fields.DatetimeField(auto_now_add=True)
    user: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="cash_movements"
    )

    class Meta:
        table = "cash_movements"
