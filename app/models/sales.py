from tortoise import fields, models


class SaleRecord(models.Model):
    id = fields.UUIDField(pk=True)
    invoice_number = fields.CharField(max_length=30, unique=True)
    at = fields.DatetimeField(auto_now_add=True)
    cashier: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="sales"
    )
    party: fields.ForeignKeyRelation["Party"] = fields.ForeignKeyField(
        "models.Party", related_name="sales"
    )
    gross = fields.DecimalField(max_digits=12, decimal_places=2)
    disc_total = fields.DecimalField(max_digits=12, decimal_places=2)
    fare = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    gst = fields.DecimalField(max_digits=12, decimal_places=2)
    grand_total = fields.DecimalField(max_digits=12, decimal_places=2)
    net_value = fields.DecimalField(max_digits=12, decimal_places=2)
    discount_override_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="discount_overrides", null=True
    )
    earned_points = fields.IntField(default=0)
    received = fields.DecimalField(max_digits=12, decimal_places=2)
    cash_back = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_credit_sale = fields.BooleanField(default=False)
    fbr_invoice_number = fields.CharField(max_length=30)
    # Client-generated idempotency key (contracts.md's architecture-doc gap, found live
    # 2026-09-12: a lost response + a still-enabled retry button could double-submit a sale —
    # double stock deduction, double credit-balance increment, double voucher redemption).
    # Optional/nullable: a caller that doesn't send one gets the old, non-idempotent behavior.
    client_request_id = fields.CharField(max_length=80, null=True, unique=True)

    class Meta:
        table = "sale_records"


class SaleLine(models.Model):
    id = fields.UUIDField(pk=True)
    sale: fields.ForeignKeyRelation[SaleRecord] = fields.ForeignKeyField(
        "models.SaleRecord", related_name="lines"
    )
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="sale_lines"
    )
    qty = fields.DecimalField(max_digits=12, decimal_places=3)
    unit_price = fields.DecimalField(max_digits=12, decimal_places=2)
    is_return = fields.BooleanField(default=False)

    class Meta:
        table = "sale_lines"


class SaleTender(models.Model):
    id = fields.UUIDField(pk=True)
    sale: fields.ForeignKeyRelation[SaleRecord] = fields.ForeignKeyField(
        "models.SaleRecord", related_name="tenders"
    )
    code = fields.CharField(max_length=20)
    amount = fields.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        table = "sale_tenders"


class ReturnRecord(models.Model):
    id = fields.UUIDField(pk=True)
    against: fields.ForeignKeyRelation[SaleRecord] = fields.ForeignKeyField(
        "models.SaleRecord", related_name="returns"
    )
    at = fields.DatetimeField(auto_now_add=True)
    cashier: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="returns_processed"
    )
    refund_total = fields.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        table = "return_records"


class ReturnLine(models.Model):
    id = fields.UUIDField(pk=True)
    return_record: fields.ForeignKeyRelation[ReturnRecord] = fields.ForeignKeyField(
        "models.ReturnRecord", related_name="lines"
    )
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="return_lines"
    )
    qty = fields.DecimalField(max_digits=12, decimal_places=3)
    unit_price = fields.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        table = "return_lines"
