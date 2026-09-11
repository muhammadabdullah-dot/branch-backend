from tortoise import fields, models


class Party(models.Model):
    id = fields.UUIDField(pk=True)
    code = fields.CharField(max_length=20, unique=True)
    name = fields.CharField(max_length=160)
    is_walk_in = fields.BooleanField(default=False)
    phone = fields.CharField(max_length=30, null=True)
    email = fields.CharField(max_length=180, null=True)
    address = fields.CharField(max_length=255, null=True)
    area = fields.CharField(max_length=120, null=True)
    contact_person = fields.CharField(max_length=120, null=True)
    ntn = fields.CharField(max_length=40, null=True)
    cnic = fields.CharField(max_length=40, null=True)
    s_tax_reg_no = fields.CharField(max_length=40, null=True)
    loyalty_no = fields.CharField(max_length=40, null=True)
    due_days = fields.IntField(default=0)
    credit_allowed = fields.BooleanField(default=False)
    credit_limit = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit_balance = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    tier = fields.CharField(max_length=20, default="retail")
    active = fields.BooleanField(default=True)

    class Meta:
        table = "parties"
