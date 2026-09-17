"""The branch's own lists and settings: what a Branch Manager keeps up instead of the software fixing it.

`ListEntry` is one value on one list. Two families share the table:

- Item lists (department, category, class, sub-class, manufacturer, brand, unit, pack unit, GST rate) and customer
  groups. The value stays written on each Item or Party as plain text, as it always was (reports, the books'
  department accounts and the head office figures all group by that text). The entry is what makes the value
  something a person can see in one place, rename on every Item at once, merge with a duplicate, or switch off
  so the Item form stops offering it. `code` is the exact text on the records.
- Reasons (stock adjustment, return to supplier, customer return). `code` is what the record stores and never
  changes; `name` is what people read and can be reworded. An adjustment reason also says whether it adds stock
  or takes it off (`effect`).

`builtin` rows came with the software and something depends on their code (the books post damaged and expired
stock to their own loss accounts), so they can be reworded and switched off but never deleted.

`ShopSetting` is one small group of settings per row (what bills print, gift voucher rules), kept as JSON so a
new setting is a new key rather than a new column.
"""
from tortoise import fields, models


class ListEntry(models.Model):
    id = fields.UUIDField(pk=True)
    kind = fields.CharField(max_length=30)
    code = fields.CharField(max_length=160)
    name = fields.CharField(max_length=160)
    # Adjustment reasons only: "add" (stock found) or "remove" (damaged, expired, lost).
    effect = fields.CharField(max_length=10, null=True)
    builtin = fields.BooleanField(default=False)
    active = fields.BooleanField(default=True)
    sort_order = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    updated_by_name = fields.CharField(max_length=120, null=True)

    class Meta:
        table = "list_entries"
        unique_together = (("kind", "code"),)


class ShopSetting(models.Model):
    key = fields.CharField(max_length=40, pk=True)
    value = fields.JSONField(default=dict)
    updated_at = fields.DatetimeField(null=True)
    updated_by_name = fields.CharField(max_length=120, null=True)

    class Meta:
        table = "shop_settings"
