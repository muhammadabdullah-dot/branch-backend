from tortoise import fields, models


class Party(models.Model):
    """A customer — the legacy Party form with Customer ticked. Suppliers are their own master."""

    id = fields.UUIDField(pk=True)
    code = fields.CharField(max_length=20, unique=True)
    name = fields.CharField(max_length=160)
    is_walk_in = fields.BooleanField(default=False)
    # Legacy CellNo. The landline is `telephone`.
    phone = fields.CharField(max_length=30, null=True)
    telephone = fields.CharField(max_length=30, null=True)
    fax = fields.CharField(max_length=30, null=True)
    email = fields.CharField(max_length=180, null=True)
    address = fields.CharField(max_length=255, null=True)
    # Legacy Address1 — a second line, usually the delivery or residence address.
    address2 = fields.CharField(max_length=255, null=True)
    city = fields.CharField(max_length=80, null=True)
    area = fields.CharField(max_length=120, null=True)
    sub_area = fields.CharField(max_length=120, null=True)
    # Legacy party category: Retail, Corporate, Doctor, Hospital, Staff … free text, suggested.
    category = fields.CharField(max_length=80, null=True)
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
    # Relative path under the media folder; see services/media_service.py.
    picture = fields.CharField(max_length=160, null=True)
    active = fields.BooleanField(default=True)

    class Meta:
        table = "parties"


class PartyContact(models.Model):
    """Legacy "Other Contact Detail": further people and numbers at the same customer."""

    id = fields.UUIDField(pk=True)
    party: fields.ForeignKeyRelation[Party] = fields.ForeignKeyField(
        "models.Party", related_name="contacts", on_delete=fields.CASCADE
    )
    cell_no = fields.CharField(max_length=30, null=True)
    contact_person = fields.CharField(max_length=120, null=True)
    email = fields.CharField(max_length=180, null=True)
    office_address = fields.CharField(max_length=255, null=True)
    res_address = fields.CharField(max_length=255, null=True)
    remarks = fields.CharField(max_length=255, null=True)
    position = fields.IntField(default=0)

    class Meta:
        table = "party_contacts"
