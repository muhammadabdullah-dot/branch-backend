"""Stock moving between this branch and head office's godown or another branch.

`direction` says which way: an **inbound** transfer is coming here — it arrives from head office by
sync, and this branch counts what came and receives it into a location. An **outbound** transfer is
this branch sending stock to another branch: dispatched here (stock leaves at once), relayed by head
office, and its receipt at the other end comes back by sync.

`origin` says where the record came from: `cloud` for transfers head office sent down, `branch` for
ones dispatched here, `local` for the demo rows seeded before sync existed (they never leave the branch).
"""
from tortoise import fields, models


class Transfer(models.Model):
    id = fields.UUIDField(pk=True)
    number = fields.CharField(max_length=30, null=True)
    direction = fields.CharField(max_length=10, default="inbound")
    origin = fields.CharField(max_length=10, default="local")
    # The other end's name as people say it: "Central Godown", "Fort Colony". Kept under its original
    # column name for the inbound view; for an outbound transfer it's the destination.
    from_warehouse = fields.CharField(max_length=120)
    counterparty_code = fields.CharField(max_length=20, null=True)
    status = fields.CharField(max_length=20)
    vehicle = fields.CharField(max_length=40, null=True)
    driver = fields.CharField(max_length=80, null=True)
    notes = fields.CharField(max_length=255, null=True)
    # Where the stock went on receipt (inbound) or left from (outbound).
    location: fields.ForeignKeyNullableRelation["Location"] = fields.ForeignKeyField(
        "models.Location", related_name="transfers", null=True, on_delete=fields.SET_NULL
    )
    dispatched_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="transfers_dispatched", null=True, on_delete=fields.SET_NULL
    )
    received_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="transfers_received", null=True, on_delete=fields.SET_NULL
    )
    # For an outbound transfer, who signed for it at the other branch (reported back by sync).
    received_by_name = fields.CharField(max_length=120, null=True)
    requested_at = fields.DatetimeField()
    dispatched_at = fields.DatetimeField(null=True)
    received_at = fields.DatetimeField(null=True)
    dispute_open = fields.BooleanField(default=False)
    dispute_note = fields.TextField(null=True)
    # An incoming shipment held back from being received, and why.
    hold_reason = fields.CharField(max_length=255, null=True)
    held_at = fields.DatetimeField(null=True)
    held_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="transfers_held", null=True, on_delete=fields.SET_NULL
    )
    # Who asked to send an outgoing shipment and who approved it, when they aren't the same person.
    requested_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="transfers_requested", null=True, on_delete=fields.SET_NULL
    )
    approved_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="transfers_approved", null=True, on_delete=fields.SET_NULL
    )
    # The receiving branch's answer before anything is sent: awaiting · acknowledged · declined, or skipped
    # (it asked for the stock itself) · overridden (head office sent it after the branch stayed offline).
    # For an inbound shipment the answer is this branch's; for an outbound one, the other branch's.
    ack_status = fields.CharField(max_length=12, null=True)
    ack_requested_at = fields.DatetimeField(null=True)
    ack_at = fields.DatetimeField(null=True)
    ack_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="transfers_answered", null=True, on_delete=fields.SET_NULL
    )
    ack_by_name = fields.CharField(max_length=120, null=True)
    ack_note = fields.CharField(max_length=255, null=True)
    override_reason = fields.CharField(max_length=255, null=True)
    override_by_name = fields.CharField(max_length=120, null=True)
    override_at = fields.DatetimeField(null=True)
    updated_at = fields.DatetimeField(auto_now=True)

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
    # The Item code the line travels by between servers — each keeps its own Item ids.
    sku = fields.CharField(max_length=60, null=True)
    qty_sent = fields.DecimalField(max_digits=12, decimal_places=3)
    qty_received = fields.DecimalField(max_digits=12, decimal_places=3, null=True)
    # What one unit cost the sender when it left — the value it travels and arrives at.
    unit_cost = fields.DecimalField(max_digits=12, decimal_places=4, null=True)

    class Meta:
        table = "transfer_lines"


class KnownBranch(models.Model):
    """The other branches this one can send stock to, as head office last listed them."""

    code = fields.CharField(max_length=20, pk=True)
    name = fields.CharField(max_length=140)
    city = fields.CharField(max_length=120, null=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "known_branches"
        ordering = ["name"]
