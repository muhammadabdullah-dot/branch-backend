from tortoise import fields, models


class Supplier(models.Model):
    """The vendor master — the legacy Party form with the Vendor / Supplier box ticked.

    Never deleted: GRNs and purchase returns name the supplier they came from. One that's no longer
    bought from is switched off, which takes it out of Receiving and Purchase Returns.

    Suppliers are one list for the whole company (services/supplier_sync_service.py). `id` and `code`
    stay this branch's own, because GRNs, orders and the supplier's ledger account point at them;
    `company_id` is the identity head office gives the supplier, the same at head office and every
    branch."""

    id = fields.CharField(max_length=40, pk=True)
    code = fields.CharField(max_length=20, unique=True)
    name = fields.CharField(max_length=160)
    contact_person = fields.CharField(max_length=120, null=True)
    phone = fields.CharField(max_length=30, null=True)
    # A second number: the owner's mobile beside the office landline.
    phone2 = fields.CharField(max_length=30, null=True)
    email = fields.CharField(max_length=180, null=True)
    address = fields.CharField(max_length=255, null=True)
    city = fields.CharField(max_length=80, null=True)
    ntn = fields.CharField(max_length=40, null=True)
    s_tax_reg_no = fields.CharField(max_length=40, null=True)
    # For a supplier who trades as a person rather than a registered business.
    cnic = fields.CharField(max_length=40, null=True)
    # Payment terms: days the branch has to pay this supplier's invoice.
    due_days = fields.IntField(default=0)
    # The discount this supplier usually gives, in percent.
    discount_percent = fields.DecimalField(max_digits=5, decimal_places=2, default=0)
    remarks = fields.CharField(max_length=255, null=True)
    active = fields.BooleanField(default=True)

    # ── the company list ──
    # Given by head office. Empty until head office has heard of this supplier.
    company_id = fields.CharField(max_length=40, null=True, unique=True)
    # Where it was first added: "HO", or the code of the branch that added it. Empty for a supplier added
    # here before there was a company list.
    origin = fields.CharField(max_length=20, null=True)
    # Head office's revision this copy was last brought up to; a message older than it changes nothing.
    rev = fields.IntField(default=0)
    updated_at = fields.DatetimeField(null=True)

    class Meta:
        table = "suppliers"
