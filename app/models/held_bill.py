from tortoise import fields, models


class HeldBill(models.Model):
    id = fields.UUIDField(pk=True)
    label = fields.CharField(max_length=120)
    lines = fields.JSONField()
    party: fields.ForeignKeyNullableRelation["Party"] = fields.ForeignKeyField(
        "models.Party", related_name="held_bills", null=True
    )
    held_at = fields.DatetimeField(auto_now_add=True)
    # Added 2026-09-18. "held" is a bill parked with F4. "slip" is a Pharmacist's pharmacy slip: the customer pays for it at
    # the cash counter, on its own and never onto a bill (services/slips_service.py). A slip stays on this table after it
    # is paid or cancelled, so its number can't be paid again.
    kind = fields.CharField(max_length=10, default="held")
    # A slip's own number, P-0042, running per branch. Printed on the slip as a barcode. Null on held bills.
    number = fields.CharField(max_length=20, null=True, unique=True)
    # The Pharmacist who made the slip. Null on held bills.
    made_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="slips_made", null=True, on_delete=fields.SET_NULL
    )
    # A slip: open (waiting at the cash counter), paid or cancelled. "on-bill" (brought onto a bill, the first way, 18 Sep)
    # is no longer set, and reads as open.
    status = fields.CharField(max_length=10, default="open")
    # The bill an on-bill slip was on. No longer set; cleared when such a slip is paid or cancelled.
    on_bill = fields.CharField(max_length=80, null=True)
    # A slip's figures (gross, disc, gst, total) and what happened to it: the bill its payment made, when, who took it and
    # how (tenders, change), or who cancelled it and why.
    slip = fields.JSONField(null=True)

    class Meta:
        table = "held_bills"
