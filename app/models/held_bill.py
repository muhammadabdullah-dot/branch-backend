from tortoise import fields, models


class HeldBill(models.Model):
    id = fields.UUIDField(pk=True)
    label = fields.CharField(max_length=120)
    lines = fields.JSONField()
    party: fields.ForeignKeyNullableRelation["Party"] = fields.ForeignKeyField(
        "models.Party", related_name="held_bills", null=True
    )
    held_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "held_bills"
