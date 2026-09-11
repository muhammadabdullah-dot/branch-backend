from tortoise import fields, models


class Supplier(models.Model):
    id = fields.CharField(max_length=40, pk=True)
    code = fields.CharField(max_length=20, unique=True)
    name = fields.CharField(max_length=160)
    contact_person = fields.CharField(max_length=120, null=True)
    phone = fields.CharField(max_length=30, null=True)

    class Meta:
        table = "suppliers"
