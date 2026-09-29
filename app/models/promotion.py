"""A campaign: this Item, for these days, at this much off.

Head office writes them and every branch receives them, the way the company's Items and suppliers already travel, so
a campaign is one decision taken once rather than a discount each shop invents. A branch never edits one; it applies
what it was sent and reports what it sold under it.

What a promotion does at the till is give a **discount**, never a different price. The till refuses any price that is
not the Item's own (services/sales_service.py `_check_price`), and that rule is worth more than the convenience of
writing a promotional price straight onto the line: a bill always shows what the Item costs and what came off it. A
campaign written as a price is turned into the discount that reaches it.

The old software's own campaigns, which these have to be able to carry: 11,364 of them, almost all a percentage off
one Item for a window of days (5% on 6,474, 10% on 3,892), a handful flat, 24 at a fixed price.

**What a campaign deliberately is not.** Its schema also offers free units, a quantity limit and a spend limit, and
not one of its 11,364 campaigns uses any of them. They were carried here for a day on the argument that they are
part of what a campaign means and a shop may want them tomorrow; that is the same argument that put a salesperson on
a bill and took it off again, so they are gone. A campaign is one Item, a window of days, and a discount.
"""
from tortoise import fields, models

# How the discount is worked out. A campaign is one of these, not a mixture.
KINDS = ("percent", "flat", "price")


class Promotion(models.Model):
    # Head office's own id for the campaign, so a campaign sent twice updates rather than doubling.
    id = fields.CharField(max_length=60, pk=True)
    code = fields.CharField(max_length=20, unique=True)
    name = fields.CharField(max_length=160)
    product: fields.ForeignKeyRelation["Product"] = fields.ForeignKeyField(  # noqa: F821
        "models.Product", related_name="promotions"
    )
    # Inclusive, on the shop's own day: a campaign that ends today still applies to tonight's selling, and to the
    # small hours after midnight where the shop's day runs past it (core/pk_time.py).
    starts_on = fields.DateField()
    ends_on = fields.DateField()
    kind = fields.CharField(max_length=10, default="percent")
    disc_percent = fields.DecimalField(max_digits=6, decimal_places=2, default=0)
    disc_flat = fields.DecimalField(max_digits=12, decimal_places=2, default=0)
    # A campaign written as "this Item is 199 while it runs". Kept as the price it names; the till turns it into the
    # discount that reaches it from whatever the Item costs that day.
    promo_price = fields.DecimalField(max_digits=12, decimal_places=2, null=True)
    # Below this many, the campaign does not apply. 1 means every one, which is what all 11,364 of theirs are: it is
    # kept because "three for the price of two" is the first thing a shop asks for, not because they used it.
    min_qty = fields.DecimalField(max_digits=12, decimal_places=3, default=1)
    active = fields.BooleanField(default=True)
    remarks = fields.CharField(max_length=255, null=True)
    # Head office's revision, so a message that arrives out of order never undoes a newer one (the same guard the
    # company supplier list uses).
    rev = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "promotions"

    def __str__(self) -> str:
        return f"{self.code} {self.name}"
