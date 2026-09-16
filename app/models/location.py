from tortoise import fields, models


class Location(models.Model):
    """A place stock sits inside this branch — the legacy Godown / Location master.

    Never deleted: every stock movement, count and adjustment names the location it happened at, and
    that history has to keep resolving to a name. A location that's no longer used is switched off
    instead, which removes it from every picker while leaving the ledger readable."""

    id = fields.CharField(max_length=40, pk=True)
    name = fields.CharField(max_length=80)
    kind = fields.CharField(max_length=20)
    priority = fields.IntField(default=1)
    active = fields.BooleanField(default=True)

    class Meta:
        table = "locations"
