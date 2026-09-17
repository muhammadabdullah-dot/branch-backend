"""This branch asking head office for stock: from the central godown, or from another branch through head office.

A request starts as a draft here (Items, quantities, a reason and a needed-by date) and can be changed or thrown
away until it is sent. Sending it goes up by sync; head office approves it (possibly with other quantities) or
declines it with a reason, and that answer comes back down. An approved request becomes a transfer, and `transfer_id`
is that transfer's id, the same here as at head office, so the request shows the shipment's progress as it goes.
"""
from tortoise import fields, models

# draft: still being written here · sent: waiting for head office · approved: a transfer is on its way ·
# declined: head office said no, and why · cancelled: withdrawn by this branch before head office decided.
REQUEST_STATUSES = ("draft", "sent", "approved", "declined", "cancelled")


class StockRequest(models.Model):
    id = fields.UUIDField(pk=True)
    number = fields.CharField(max_length=30, unique=True)
    status = fields.CharField(max_length=12, default="draft")
    # Who is asked to send it: null is head office's central godown, otherwise another branch's code.
    source_code = fields.CharField(max_length=20, null=True)
    source_name = fields.CharField(max_length=140, null=True)
    reason = fields.CharField(max_length=255, null=True)
    needed_by = fields.DateField(null=True)
    created_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="stock_requests", null=True, on_delete=fields.SET_NULL
    )
    created_by_name = fields.CharField(max_length=120, null=True)
    sent_at = fields.DatetimeField(null=True)
    sent_by_name = fields.CharField(max_length=120, null=True)
    # Head office's own number for it (REQ-0033), once head office has it.
    head_office_number = fields.CharField(max_length=30, null=True)
    received_at_head_office = fields.DatetimeField(null=True)
    decided_at = fields.DatetimeField(null=True)
    decided_by_name = fields.CharField(max_length=120, null=True)
    # Why it was declined, or what head office changed when approving.
    decision_note = fields.CharField(max_length=255, null=True)
    transfer_id = fields.CharField(max_length=36, null=True)
    transfer_number = fields.CharField(max_length=30, null=True)
    cancelled_at = fields.DatetimeField(null=True)
    # "branch" when written here; "head-office" when head office recorded it for this branch.
    origin = fields.CharField(max_length=12, default="branch")
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "stock_requests"
        ordering = ["-created_at"]


class StockRequestLine(models.Model):
    id = fields.UUIDField(pk=True)
    request: fields.ForeignKeyRelation[StockRequest] = fields.ForeignKeyField(
        "models.StockRequest", related_name="lines", on_delete=fields.CASCADE
    )
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="request_lines"
    )
    # The Item code the line travels by; each server keeps its own Item ids.
    sku = fields.CharField(max_length=60)
    qty_requested = fields.DecimalField(max_digits=12, decimal_places=3)
    # What head office approved. Null until it decides; 0 means it left this Item out.
    qty_approved = fields.DecimalField(max_digits=12, decimal_places=3, null=True)
    # What the branch held and sold a day when the request was sent, so head office decides on the same picture.
    on_hand = fields.DecimalField(max_digits=12, decimal_places=3, null=True)
    daily_sales = fields.DecimalField(max_digits=12, decimal_places=3, null=True)

    class Meta:
        table = "stock_request_lines"
