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
    # The drawer this bill was rung into. It is what lets two counters trade at once and still each
    # reconcile: without it the only way to tell whose cash this is would be the clock.
    till_session: fields.ForeignKeyNullableRelation["TillSession"] = fields.ForeignKeyField(
        "models.TillSession", related_name="sales", null=True, on_delete=fields.SET_NULL
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
    # The D.Marina member the bill was rung up for, when there was one.
    member: fields.ForeignKeyNullableRelation["Member"] = fields.ForeignKeyField(
        "models.Member", related_name="sales", null=True, on_delete=fields.SET_NULL
    )
    points_redeemed = fields.IntField(default=0)
    received = fields.DecimalField(max_digits=12, decimal_places=2)
    cash_back = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_credit_sale = fields.BooleanField(default=False)
    fbr_invoice_number = fields.CharField(max_length=30)
    # Client-generated idempotency key (contracts.md's architecture-doc gap, found live
    # 2026-09-12: a lost response + a still-enabled retry button could double-submit a sale:
    # double stock deduction, double credit-balance increment, double voucher redemption).
    # Optional/nullable: a caller that doesn't send one gets the old, non-idempotent behavior.
    client_request_id = fields.CharField(max_length=80, null=True, unique=True)

    class Meta:
        table = "sale_records"
        # Every report and figure asks for the bills of a period first; without this each one reads every bill ever rung.
        indexes = (("at",),)


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
    # The alternate (pack) barcode the line was rung up by, when it was one: its pack discount applied.
    alias_code = fields.CharField(max_length=60, null=True)
    # Everything taken off this line: the Item's own discount plus its share of the bill discount.
    # Null on sales from before this was recorded.
    disc_amount = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    # The Item's average cost at the moment of sale, so profit reports don't drift as costs move.
    unit_cost = fields.DecimalField(max_digits=12, decimal_places=4, null=True)
    # The GST charged on this line (after its discount). Null on sales from before this was recorded.
    tax_amount = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    # Added 2026-09-18. A line sold in other amounts than the stocked unit (services/sell_levels.py): "piece" or "strip"
    # (the unit opened and sold loose), or "pack" or "box" (several units together); how many of them, the price of one,
    # and what one is in plain words ("tablet", "10" pieces to a strip, "12" units to a pack, "10 x 12" for a box).
    # `qty` stays in stocked units (5 tablets of a 200-tablet box is 0.025) and `unit_price` the price of one unit, so
    # every figure that counts units still adds up. Null on lines sold by the stocked unit.
    sell_level = fields.CharField(max_length=8, null=True)
    # Added 2026-09-18. A return line rung on Billing names the bill its goods came from, so the return window and what
    # that bill has left to return are checked against it. Null on sold lines and on return lines from before.
    return_of_invoice = fields.CharField(max_length=30, null=True)
    level_qty = fields.DecimalField(max_digits=12, decimal_places=3, null=True)
    level_price = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    level_detail = fields.CharField(max_length=30, null=True)

    class Meta:
        table = "sale_lines"
        # A bill's lines, and one Item's sales over a period (ABC and XYZ, the Item drill-down).
        indexes = (("sale",), ("product",))


class SaleTender(models.Model):
    id = fields.UUIDField(pk=True)
    sale: fields.ForeignKeyRelation[SaleRecord] = fields.ForeignKeyField(
        "models.SaleRecord", related_name="tenders"
    )
    code = fields.CharField(max_length=20)
    amount = fields.DecimalField(max_digits=12, decimal_places=2)
    # What proves the payment. Card: the last 4 digits from the machine's shop copy. Easypaisa / JazzCash:
    # the paying account's number. Bank transfer: null (see transaction_id and account).
    reference = fields.CharField(max_length=40, null=True)
    # The transfer's transaction ID: required for a bank transfer, optional for a wallet.
    transaction_id = fields.CharField(max_length=60, null=True)
    # The shop account a bank transfer went into.
    account = fields.CharField(max_length=80, null=True)
    # The customer's screenshot of a bank transfer, under the media folder.
    proof = fields.CharField(max_length=160, null=True)

    class Meta:
        table = "sale_tenders"
        indexes = (("sale",),)


class ReturnRecord(models.Model):
    id = fields.UUIDField(pk=True)
    against: fields.ForeignKeyRelation[SaleRecord] = fields.ForeignKeyField(
        "models.SaleRecord", related_name="returns"
    )
    at = fields.DatetimeField(auto_now_add=True)
    cashier: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="returns_processed"
    )
    till_session: fields.ForeignKeyNullableRelation["TillSession"] = fields.ForeignKeyField(
        "models.TillSession", related_name="returns", null=True, on_delete=fields.SET_NULL
    )
    refund_total = fields.DecimalField(max_digits=12, decimal_places=2)
    # How the money went back. CASH comes out of the drawer; VOUCHER goes back onto the gift voucher that
    # paid (refund_reference is its code) and never touches the drawer or cash figures.
    refund_method = fields.CharField(max_length=20, default="CASH")
    refund_reference = fields.CharField(max_length=40, null=True)
    # Why the goods came back, from the branch's list of customer return reasons (a ListEntry code). Null on
    # returns taken before reasons were recorded, and when none was picked.
    reason = fields.CharField(max_length=40, null=True)
    # The remark typed with the reason Other (5 to 20 characters), or why a sale was reversed onto its voucher.
    note = fields.TextField(null=True)
    # The GST inside refund_total, and the rupee rounding the refund took. Null on older returns.
    tax_total = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    rounding = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    # The number printed on the return receipt (e.g. MT-RT-2026-000001). Null on returns taken before receipts.
    number = fields.CharField(max_length=30, null=True)
    # refund (money back), replace (the same Items went back to the customer) or exchange (other Items went out).
    # Null on older returns, which were all refunds.
    kind = fields.CharField(max_length=10, null=True)
    # For a replace or an exchange: the bill the Items going out were rung on. The return's refund paid for it in
    # the same way (refund_method), so the drawer and the books only move by the difference.
    exchange_sale: fields.ForeignKeyNullableRelation[SaleRecord] = fields.ForeignKeyField(
        "models.SaleRecord", related_name="exchange_returns", null=True, on_delete=fields.SET_NULL
    )

    class Meta:
        table = "return_records"
        # Returns in a period, and the returns taken against one bill.
        indexes = (("at",), ("against",))


class ReturnLine(models.Model):
    id = fields.UUIDField(pk=True)
    return_record: fields.ForeignKeyRelation[ReturnRecord] = fields.ForeignKeyField(
        "models.ReturnRecord", related_name="lines"
    )
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(
        "models.Product", related_name="return_lines"
    )
    qty = fields.DecimalField(max_digits=12, decimal_places=3)
    # What the customer actually paid per unit, GST included, after the bill's discounts.
    unit_price = fields.DecimalField(max_digits=12, decimal_places=2)
    # The GST inside this line's refund, and the unit cost the goods go back on the shelf at.
    tax_amount = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    unit_cost = fields.DecimalField(max_digits=12, decimal_places=4, null=True)

    class Meta:
        table = "return_lines"
        indexes = (("return_record",), ("product",))
