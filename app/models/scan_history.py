"""Scan history: every Item put on a bill at the till, and how its line ended.

A line starts when an Item goes on a bill (scanned, picked from Find, weighed, or brought back by recalling a held bill)
and ends sold, taken off, or held. A line still open with no sale behind it is a bill that was never paid. Each change
to the line (more, fewer, piece to pack, taken off) is an event under it, with who, when, which till and counter, and
which device. Written by the till in batches (POST /sales/scan-events); a sale marks its bill's open lines sold.

Kept on the branch only for now: head office doesn't receive it.
"""
from tortoise import fields, models


class ScanLine(models.Model):
    id = fields.UUIDField(pk=True)
    # The bill on the till: its POST /sales idempotency key, which a sale made from it carries as client_request_id.
    bill_id = fields.CharField(max_length=80)
    # The line's number on that bill. A number can come round again after its line is taken off; each new line is a
    # new row, and changes go to the latest open line with the number.
    line_key = fields.IntField()
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="scan_lines", on_delete=fields.CASCADE
    )
    user: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField("models.User", related_name="scan_lines")
    till_session: fields.ForeignKeyNullableRelation["TillSession"] = fields.ForeignKeyField(
        "models.TillSession", related_name="scan_lines", null=True, on_delete=fields.SET_NULL
    )
    counter: fields.ForeignKeyNullableRelation["SalesCounter"] = fields.ForeignKeyField(
        "models.SalesCounter", related_name="scan_lines", null=True, on_delete=fields.SET_NULL
    )
    device_id = fields.CharField(max_length=80, null=True)
    # How it went on: "scan", "search", "weight", "recall", or "slip" (a pharmacy slip paid at the cash counter).
    how = fields.CharField(max_length=12, default="scan")
    first_at = fields.DatetimeField()
    last_at = fields.DatetimeField()
    # Pieces on the line now, or when it ended; and "pack" or "box" when it was sold that way.
    qty = fields.DecimalField(max_digits=12, decimal_places=3)
    level = fields.CharField(max_length=8, null=True)
    # "open", "removed", "held" or "sold". An open line on a bill that was never paid reads as never paid.
    outcome = fields.CharField(max_length=12, default="open")
    ended_at = fields.DatetimeField(null=True)
    invoice_number = fields.CharField(max_length=30, null=True)

    class Meta:
        table = "scan_lines"
        indexes = (("first_at",), ("user", "first_at"), ("bill_id", "line_key"))


class ScanEvent(models.Model):
    # The till's own id for the event, so one sent twice is kept once.
    id = fields.CharField(max_length=40, pk=True)
    line: fields.ForeignKeyRelation[ScanLine] = fields.ForeignKeyField("models.ScanLine", related_name="events", on_delete=fields.CASCADE)
    at = fields.DatetimeField()
    # "added", "more", "less", "level", "removed" or "held".
    action = fields.CharField(max_length=12)
    qty = fields.DecimalField(max_digits=12, decimal_places=3)
    level = fields.CharField(max_length=8, null=True)

    class Meta:
        table = "scan_events"
        indexes = (("line",),)
