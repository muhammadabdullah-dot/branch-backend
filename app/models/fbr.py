"""FBR invoices: the branch's link to FBR's POS integration, and the FBR invoice issued for each bill and return.

`FbrSettings` is one row: how this branch issues FBR invoices (off, dummy test numbers, FBR's sandbox, or production),
the POS ID FBR registered for each counter, and the access token FBR issued. The token is a secret: it stays in this
branch's database, is never sent back to a screen in full, never written to a log, and never goes to head office
(nothing in sync reads this table). Only a Branch Manager changes the row (services/fbr_service.py).

`FbrInvoice` is one row per bill or return that was given an FBR invoice: the number printed on the receipt (a dummy
test number, or the one FBR sent back), what was sent and what FBR answered, and, while FBR can't be reached, when it
will be tried again. Added 2026-09-21; bills and returns from before have no row.
"""
from tortoise import fields, models


class FbrSettings(models.Model):
    # Always 1: the branch has one set of FBR settings.
    id = fields.IntField(pk=True)
    # off: no FBR invoices. dummy: a test number per bill, never sent anywhere. sandbox: FBR's test system.
    # production: real FBR invoices.
    mode = fields.CharField(max_length=12, default="dummy")
    # The bearer token FBR (PRAL) issued for this business. Secret: see the module note.
    access_token = fields.TextField(null=True)
    # Counter id -> the POS ID FBR registered for that terminal (digits): {"<SalesCounter id>": "110014"}.
    pos_ids = fields.JSONField(default=dict)
    # Printed on every receipt, as SRO 1006(I)/2021 asks: the tax office the business is registered at.
    tax_office = fields.CharField(max_length=80, null=True)
    # FBR wants a PCT (customs tariff) code on every line; Items don't carry one yet, so this stands in.
    default_pct_code = fields.CharField(max_length=8, null=True)
    updated_at = fields.DatetimeField(null=True)
    updated_by_name = fields.CharField(max_length=120, null=True)

    class Meta:
        table = "fbr_settings"


class FbrInvoice(models.Model):
    id = fields.UUIDField(pk=True)
    # sale (a bill) or return (a credit note against a bill).
    kind = fields.CharField(max_length=8)
    sale: fields.ForeignKeyNullableRelation["SaleRecord"] = fields.ForeignKeyField(
        "models.SaleRecord", related_name="fbr_invoices", null=True, on_delete=fields.CASCADE
    )
    return_record: fields.ForeignKeyNullableRelation["ReturnRecord"] = fields.ForeignKeyField(
        "models.ReturnRecord", related_name="fbr_invoices", null=True, on_delete=fields.CASCADE
    )
    # The POS's own number for it (FBR calls it USIN): the bill number, or the return number. A return's RefUSIN is the
    # bill it is against.
    usin = fields.CharField(max_length=50)
    ref_usin = fields.CharField(max_length=50, null=True)
    # The counter it was rung at, and that counter's FBR POS ID when it was issued.
    counter_id = fields.CharField(max_length=40, null=True)
    pos_id = fields.CharField(max_length=20, null=True)
    # The mode it was issued in: dummy, sandbox or production.
    mode = fields.CharField(max_length=12)
    # dummy: a test number, never sent. waiting: not yet accepted by FBR (queued, tried again on its own). posted: FBR
    # sent its number back. refused: FBR answered with an error; it waits for a person to fix the cause and send again.
    status = fields.CharField(max_length=10)
    # What the receipt prints: the dummy test number, or FBR's own number once posted. Null while waiting.
    fbr_invoice_number = fields.CharField(max_length=60, null=True, unique=True)
    # What was sent (the documented invoice JSON) and what FBR answered, as they were.
    payload = fields.JSONField(null=True)
    response = fields.JSONField(null=True)
    attempts = fields.IntField(default=0)
    last_error = fields.CharField(max_length=300, null=True)
    next_try_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    posted_at = fields.DatetimeField(null=True)

    class Meta:
        table = "fbr_invoices"
        # The queue is read by status and when it is due; a bill's or a return's invoice by the record.
        indexes = (("status", "next_try_at"), ("sale",), ("return_record",))
