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
    # Which counter this drawer belongs to. Null on sessions opened before counters existed, and on a
    # branch that has not set any up.
    counter: fields.ForeignKeyNullableRelation["SalesCounter"] = fields.ForeignKeyField(
        "models.SalesCounter", related_name="sessions", null=True, on_delete=fields.SET_NULL
    )
    status = fields.CharField(max_length=10, default="open")
    net_cash = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    counted_cash = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    variance = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    closing_denominations = fields.JSONField(null=True)
    closed_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="closed_tills", null=True, on_delete=fields.SET_NULL
    )

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
    # What the money was for (cash out: an expense head, a supplier, the safe) or where it came from (cash in).
    account: fields.ForeignKeyNullableRelation["Account"] = fields.ForeignKeyField(
        "models.Account", related_name="cash_movements", null=True, on_delete=fields.SET_NULL
    )
    payee = fields.CharField(max_length=120, null=True)

    class Meta:
        table = "cash_movements"
