"""The branch's books — a chart of accounts in the old software's five levels, and vouchers.

Account Type → Account Category → Account Group → Sub Group → Account, as in MULTI SOFT. Types and
categories are the company's standard and come with the software; groups, sub groups and accounts can be
added here. Every credit customer and every supplier has an account of its own under its group, so the
trial balance lists them by name.

Nothing is ever written straight into a balance. A voucher is the only way money moves in the books:
- manual vouchers (cash / bank payment and receipt, journal, contra, opening balances) are saved as drafts
  and posted by someone who may post;
- automatic vouchers are generated from the branch's own records — a day's sales, a till close, a GRN, a
  transfer — and regenerated if those records change, so the books can always be rebuilt to the rupee.
A posted voucher is never edited: it is reversed, and both stay on the record.
"""
from tortoise import fields, models


class AccountType(models.Model):
    code = fields.CharField(max_length=2, pk=True)
    name = fields.CharField(max_length=60)
    # Which side a balance normally sits on: debit (assets, expenses) or credit (liabilities, equity, revenue).
    nature = fields.CharField(max_length=6)
    # balance → balance sheet, income → income statement.
    statement = fields.CharField(max_length=10)

    class Meta:
        table = "acc_types"


class AccountCategory(models.Model):
    code = fields.CharField(max_length=4, pk=True)
    name = fields.CharField(max_length=80)
    type: fields.ForeignKeyRelation[AccountType] = fields.ForeignKeyField("models.AccountType", related_name="categories")

    class Meta:
        table = "acc_categories"


class AccountGroup(models.Model):
    code = fields.CharField(max_length=6, pk=True)
    name = fields.CharField(max_length=100)
    category: fields.ForeignKeyRelation[AccountCategory] = fields.ForeignKeyField("models.AccountCategory", related_name="groups")
    priority = fields.IntField(default=0)
    manual_code = fields.CharField(max_length=30, null=True)
    # Part of the standard chart every branch and head office share.
    standard = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "acc_groups"


class AccountSubGroup(models.Model):
    code = fields.CharField(max_length=8, pk=True)
    name = fields.CharField(max_length=100)
    group: fields.ForeignKeyRelation[AccountGroup] = fields.ForeignKeyField("models.AccountGroup", related_name="sub_groups")
    standard = fields.BooleanField(default=False)

    class Meta:
        table = "acc_sub_groups"


class Account(models.Model):
    id = fields.UUIDField(pk=True)
    code = fields.CharField(max_length=12, unique=True)
    name = fields.CharField(max_length=160)
    group: fields.ForeignKeyRelation[AccountGroup] = fields.ForeignKeyField("models.AccountGroup", related_name="accounts")
    sub_group: fields.ForeignKeyNullableRelation[AccountSubGroup] = fields.ForeignKeyField(
        "models.AccountSubGroup", related_name="accounts", null=True, on_delete=fields.SET_NULL
    )
    # general · cash · bank · wallet · customer · supplier · interoffice
    kind = fields.CharField(max_length=12, default="general")
    # The accounts automatic vouchers post to ("cash.counter", "sales.dept.grocery", "customer:<party id>").
    system_key = fields.CharField(max_length=80, unique=True, null=True)
    # The customer, supplier or branch this account belongs to.
    party_ref = fields.CharField(max_length=60, null=True)
    active = fields.BooleanField(default=True)
    # Kept out of manual vouchers: only the software posts to it.
    restricted = fields.BooleanField(default=False)
    check_limit = fields.BooleanField(default=False)
    balance_limit = fields.DecimalField(max_digits=16, decimal_places=2, null=True)
    bank_name = fields.CharField(max_length=80, null=True)
    bank_account_no = fields.CharField(max_length=40, null=True)
    manual_code = fields.CharField(max_length=30, null=True)
    remarks = fields.CharField(max_length=255, null=True)
    standard = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "acc_accounts"


class Voucher(models.Model):
    id = fields.UUIDField(pk=True)
    number = fields.CharField(max_length=30, unique=True)
    # CPV CRV BPV BRV JV CV OB (manual) · SV PV PRV TV STV TRV GVV (automatic)
    vtype = fields.CharField(max_length=4)
    date = fields.DateField()
    # draft · posted · cancelled
    status = fields.CharField(max_length=10, default="draft")
    auto = fields.BooleanField(default=False)
    # What an automatic voucher was generated from ("sales-day:2026-09-15", "grn:<id>") — one voucher per source.
    source = fields.CharField(max_length=120, unique=True, null=True)
    source_hash = fields.CharField(max_length=64, null=True)
    # The cash or bank account a cash/bank voucher pays from or receives into.
    header_account: fields.ForeignKeyNullableRelation[Account] = fields.ForeignKeyField(
        "models.Account", related_name="header_vouchers", null=True, on_delete=fields.SET_NULL
    )
    reference_no = fields.CharField(max_length=60, null=True)
    description = fields.CharField(max_length=500, null=True)
    cheque_no = fields.CharField(max_length=30, null=True)
    cheque_date = fields.DateField(null=True)
    total = fields.DecimalField(max_digits=16, decimal_places=2, default=0)
    created_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="vouchers_created", null=True, on_delete=fields.SET_NULL
    )
    created_by_name = fields.CharField(max_length=120, null=True)
    posted_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="vouchers_posted", null=True, on_delete=fields.SET_NULL
    )
    posted_by_name = fields.CharField(max_length=120, null=True)
    posted_at = fields.DatetimeField(null=True)
    cancelled_by_name = fields.CharField(max_length=120, null=True)
    cancelled_at = fields.DatetimeField(null=True)
    cancel_reason = fields.CharField(max_length=255, null=True)
    # The voucher this one reverses (its id).
    reversal_of_id = fields.CharField(max_length=36, null=True)
    reversed = fields.BooleanField(default=False)
    version = fields.IntField(default=1)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "acc_vouchers"
        indexes = (("date", "status"),)


class VoucherLine(models.Model):
    id = fields.UUIDField(pk=True)
    voucher: fields.ForeignKeyRelation[Voucher] = fields.ForeignKeyField("models.Voucher", related_name="lines", on_delete=fields.CASCADE)
    line_no = fields.IntField(default=0)
    account: fields.ForeignKeyRelation[Account] = fields.ForeignKeyField("models.Account", related_name="voucher_lines", on_delete=fields.RESTRICT)
    debit = fields.DecimalField(max_digits=16, decimal_places=2, default=0)
    credit = fields.DecimalField(max_digits=16, decimal_places=2, default=0)
    description = fields.CharField(max_length=255, null=True)
    reference_no = fields.CharField(max_length=60, null=True)

    class Meta:
        table = "acc_voucher_lines"


class AccountsSettings(models.Model):
    id = fields.IntField(pk=True)
    # The month the financial year starts in — July for Pakistan unless changed.
    fiscal_start_month = fields.IntField(default=7)
    # Automatic vouchers are generated for records on or after this day. Opening balances sit the day before.
    books_start = fields.DateField(null=True)
    # Closed months: nothing dated on or before this day can be posted, changed or reversed.
    locked_until = fields.DateField(null=True)
    # Which account each payment method's money goes to, when it isn't the standard one: {"CARD": "<account id>"}.
    tender_accounts = fields.JSONField(default=dict)
    last_posting_at = fields.DatetimeField(null=True)
    last_posting_note = fields.CharField(max_length=255, null=True)
    posting_problems = fields.JSONField(default=list)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "acc_settings"


class CustomerPayment(models.Model):
    """Money a credit customer pays against what they owe. Cash goes into the open till."""

    id = fields.UUIDField(pk=True)
    number = fields.CharField(max_length=20, unique=True)
    party: fields.ForeignKeyRelation["Party"] = fields.ForeignKeyField("models.Party", related_name="payments")
    amount = fields.DecimalField(max_digits=12, decimal_places=2)
    # CASH · CARD · BANK · EASYPAISA · JAZZCASH
    method = fields.CharField(max_length=12)
    reference = fields.CharField(max_length=60, null=True)
    note = fields.CharField(max_length=255, null=True)
    received_by: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField("models.User", related_name="customer_payments")
    cash_movement: fields.ForeignKeyNullableRelation["CashMovement"] = fields.ForeignKeyField(
        "models.CashMovement", related_name="customer_payments", null=True, on_delete=fields.SET_NULL
    )
    balance_after = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "customer_payments"


class Cheque(models.Model):
    """A cheque received from a customer (or anyone paying the branch) — held, deposited, cleared or bounced."""

    id = fields.UUIDField(pk=True)
    number = fields.CharField(max_length=20, unique=True)
    direction = fields.CharField(max_length=10, default="received")
    # The customer or other account the cheque came from.
    party_account: fields.ForeignKeyRelation[Account] = fields.ForeignKeyField("models.Account", related_name="cheques")
    # The branch's bank account it was deposited into.
    bank_account: fields.ForeignKeyNullableRelation[Account] = fields.ForeignKeyField(
        "models.Account", related_name="cheques_deposited", null=True, on_delete=fields.SET_NULL
    )
    cheque_no = fields.CharField(max_length=30)
    drawn_on = fields.CharField(max_length=80, null=True)
    cheque_date = fields.DateField()
    received_on = fields.DateField()
    amount = fields.DecimalField(max_digits=14, decimal_places=2)
    # pending · cleared · bounced · cancelled
    status = fields.CharField(max_length=10, default="pending")
    cleared_on = fields.DateField(null=True)
    bounced_on = fields.DateField(null=True)
    note = fields.CharField(max_length=255, null=True)
    created_by_name = fields.CharField(max_length=120, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "cheques"
