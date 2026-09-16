"""D.Marina members — the people behind the bills, and the points they earn.

A member is anyone who has given a name and a mobile number at the till: everyone who pays by card or
online (their details are always taken), and anyone paying some other way who chose to join. It is a
different record from a Party. A Party is a trading account — wholesale, credit, a supplier-like
customer with terms; a member is a shopper. When a Party's person joins, the member is linked to it.

The code is this branch's own code and a running number (MT-000042), so two branches creating members
while offline can never hand out the same code. One mobile number is one member.
"""
from tortoise import fields, models

# How someone became a member. Kept for good — paying by card later doesn't change how they joined.
JOINED_VIA = ("card", "online", "till", "members-screen")


class Member(models.Model):
    id = fields.UUIDField(pk=True)
    code = fields.CharField(max_length=20, unique=True)
    name = fields.CharField(max_length=120)
    # Digits only, local form (03001234567). One number, one member.
    phone = fields.CharField(max_length=20, unique=True)
    joined_via = fields.CharField(max_length=20)
    # The payment that made them a member (CARD, EASYPAISA, JAZZCASH, BANK) — or null for one who chose to.
    joined_method = fields.CharField(max_length=20, null=True)
    # The branch that signed them up. A member from another branch arrives through head office.
    home_branch_code = fields.CharField(max_length=10)
    party: fields.ForeignKeyNullableRelation["Party"] = fields.ForeignKeyField(
        "models.Party", related_name="members", null=True, on_delete=fields.SET_NULL
    )
    # What `LoyaltyEntry` rows add up to, kept on the member so the till never sums a history to show it.
    points_balance = fields.IntField(default=0)
    active = fields.BooleanField(default=True)
    created_by_name = fields.CharField(max_length=120, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "members"


class LoyaltyEntry(models.Model):
    """One movement of points. Its id travels with it to head office and on to other branches, so the
    same earning can never be counted twice anywhere."""

    id = fields.UUIDField(pk=True)
    member: fields.ForeignKeyRelation[Member] = fields.ForeignKeyField(
        "models.Member", related_name="entries", on_delete=fields.CASCADE
    )
    # earn (a sale), redeem (spent on a bill), reverse (goods returned), adjust (set by hand)
    kind = fields.CharField(max_length=10)
    points = fields.IntField()
    invoice_number = fields.CharField(max_length=30, null=True)
    branch_code = fields.CharField(max_length=10)
    note = fields.CharField(max_length=255, null=True)
    by_name = fields.CharField(max_length=120, null=True)
    at = fields.DatetimeField()

    class Meta:
        table = "loyalty_entries"


class LoyaltySettings(models.Model):
    """How points are earned and what they're worth. Set here or at head office; the later change wins
    everywhere."""

    id = fields.IntField(pk=True)
    enabled = fields.BooleanField(default=True)
    # Rupees spent for one point.
    rupees_per_point = fields.DecimalField(max_digits=10, decimal_places=2, default=100)
    # Rupees one point takes off a bill.
    point_value = fields.DecimalField(max_digits=10, decimal_places=2, default=1)
    min_redeem_points = fields.IntField(default=100)
    # The most of a bill points may pay for.
    max_redeem_percent = fields.DecimalField(max_digits=5, decimal_places=2, default=50)
    updated_at = fields.DatetimeField(null=True)
    updated_by_name = fields.CharField(max_length=120, null=True)
    # Where the rules were last set: this branch's code, or HO for head office.
    updated_from = fields.CharField(max_length=10, null=True)

    class Meta:
        table = "loyalty_settings"
