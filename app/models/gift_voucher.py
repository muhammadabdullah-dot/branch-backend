from tortoise import fields, models


class GiftVoucher(models.Model):
    id = fields.UUIDField(pk=True)
    code = fields.CharField(max_length=20, unique=True)
    face_value = fields.DecimalField(max_digits=12, decimal_places=2)
    balance = fields.DecimalField(max_digits=12, decimal_places=2)
    # The customer this voucher belongs to, if it belongs to anyone. Null is an open (bearer) gift
    # voucher: somebody buys it for someone else, and whoever holds the code spends it. Set, and it
    # is that customer's alone. A credit note or a loyalty reward handed to Sana Bibi must not be
    # spendable on Ali Traders' bill by whoever happened to see the code.
    party = fields.ForeignKeyField(
        "models.Party", related_name="gift_vouchers", null=True, on_delete=fields.RESTRICT
    )
    # Kept, and filled from the party on issue. It is what printed receipts and older vouchers
    # carry, and a customer renamed later should not rewrite what the voucher said when issued.
    issued_to_name = fields.CharField(max_length=160, null=True)
    issued_at = fields.DatetimeField(auto_now_add=True)
    expires_at = fields.DatetimeField()
    status = fields.CharField(max_length=10, default="active")
    # How it was paid for: CASH (into the till) · CARD · BANK · EASYPAISA · JAZZCASH, or COMPLIMENTARY (given free).
    # Null on vouchers issued before payment was recorded.
    paid_by = fields.CharField(max_length=20, null=True)
    payment_reference = fields.CharField(max_length=60, null=True)
    issued_by_name = fields.CharField(max_length=120, null=True)

    class Meta:
        table = "gift_vouchers"


class VoucherRedemption(models.Model):
    id = fields.UUIDField(pk=True)
    voucher: fields.ForeignKeyRelation[GiftVoucher] = fields.ForeignKeyField(
        "models.GiftVoucher", related_name="redemptions"
    )
    invoice_number = fields.CharField(max_length=30)
    amount = fields.DecimalField(max_digits=12, decimal_places=2)
    at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "voucher_redemptions"
