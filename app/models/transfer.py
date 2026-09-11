"""Inbound-only view for now — branch-local seed data, per contracts.md §3.4/§5.2: this becomes a
read of the shared cross-service Transfer once I7's sync lands, not a locally-owned table forever."""
from tortoise import fields, models


class Transfer(models.Model):
    id = fields.UUIDField(pk=True)
    from_warehouse = fields.CharField(max_length=120)
    status = fields.CharField(max_length=20)
    vehicle = fields.CharField(max_length=40, null=True)
    driver = fields.CharField(max_length=80, null=True)
    requested_at = fields.DatetimeField()
    dispatched_at = fields.DatetimeField(null=True)
    received_at = fields.DatetimeField(null=True)
    dispute_open = fields.BooleanField(default=False)
    dispute_note = fields.TextField(null=True)

    class Meta:
        table = "transfers"


class TransferLine(models.Model):
    id = fields.UUIDField(pk=True)
    transfer: fields.ForeignKeyRelation[Transfer] = fields.ForeignKeyField(
        "models.Transfer", related_name="lines"
    )
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="transfer_lines"
    )
    qty_sent = fields.DecimalField(max_digits=12, decimal_places=3)
    qty_received = fields.DecimalField(max_digits=12, decimal_places=3, null=True)

    class Meta:
        table = "transfer_lines"
