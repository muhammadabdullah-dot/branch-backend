from tortoise import fields, models


class GiftVoucher(models.Model):
    id = fields.UUIDField(pk=True)
    code = fields.CharField(max_length=20, unique=True)
    face_value = fields.DecimalField(max_digits=12, decimal_places=2)
    balance = fields.DecimalField(max_digits=12, decimal_places=2)
    issued_to_name = fields.CharField(max_length=160, null=True)
    issued_at = fields.DatetimeField(auto_now_add=True)
    expires_at = fields.DatetimeField()
    status = fields.CharField(max_length=10, default="active")

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
