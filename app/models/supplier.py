from tortoise import fields, models


class Supplier(models.Model):
    """The vendor master — the legacy Party form with the Vendor / Supplier box ticked.

    Never deleted: GRNs and purchase returns name the supplier they came from. One that's no longer
    bought from is switched off, which takes it out of Receiving and Purchase Returns."""

    id = fields.CharField(max_length=40, pk=True)
    code = fields.CharField(max_length=20, unique=True)
    name = fields.CharField(max_length=160)
    contact_person = fields.CharField(max_length=120, null=True)
    phone = fields.CharField(max_length=30, null=True)
    email = fields.CharField(max_length=180, null=True)
    address = fields.CharField(max_length=255, null=True)
    city = fields.CharField(max_length=80, null=True)
    ntn = fields.CharField(max_length=40, null=True)
    s_tax_reg_no = fields.CharField(max_length=40, null=True)
    # Payment terms: days the branch has to pay this supplier's invoice.
    due_days = fields.IntField(default=0)
    remarks = fields.CharField(max_length=255, null=True)
    active = fields.BooleanField(default=True)

    class Meta:
        table = "suppliers"
