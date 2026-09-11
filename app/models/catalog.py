"""Reference data — Product/PaymentMethod. Seeded once; no write endpoints in I3 (catalog management
is out of this iteration's scope). Shape matches contracts.md §3.1 / frontend-baseline.md §2.2."""
from tortoise import fields, models


class Product(models.Model):
    id = fields.CharField(max_length=40, pk=True)
    sku = fields.CharField(max_length=40, unique=True)
    name = fields.CharField(max_length=160)
    price = fields.DecimalField(max_digits=12, decimal_places=2)
    tax_rate = fields.DecimalField(max_digits=5, decimal_places=2)
    is_weighed = fields.BooleanField(default=False)
    unit = fields.CharField(max_length=20)
    barcode = fields.CharField(max_length=40, null=True, unique=True)
    pack_unit = fields.CharField(max_length=40, null=True)
    pack_size = fields.IntField(null=True)

    class Meta:
        table = "products"


class PaymentMethod(models.Model):
    code = fields.CharField(max_length=20, pk=True)
    name = fields.CharField(max_length=60)
    kind = fields.CharField(max_length=20)

    class Meta:
        table = "payment_methods"
