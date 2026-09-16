"""The counters on the shop floor, and who is on duty at each.

A counter is the place a sale is rung from — "Counter 1", the pharmacy window, the gift desk. It is a
first-class record because everything an owner wants to ask about the floor is asked about a place:
which counters are open, who is standing at each, what each has taken, whose drawer is short.

Duty is kept as time-bounded history rather than a flag on a person: "Hina was on Counter 2 from 09:05
to 14:30, put there by the Sales Manager" answers a question afterwards that a current-state flag cannot.
A person is on duty exactly while a duty row has no `ended_at`.

Note the name: `Counter` is already taken in this database by the document-numbering sequence
(`app/models/sequence.py`), which is a different thing entirely.
"""
from tortoise import fields, models


class SalesCounter(models.Model):
    id = fields.UUIDField(pk=True)
    code = fields.CharField(max_length=20, unique=True)
    name = fields.CharField(max_length=80)
    # Where it stands, in the branch's own words: "front left", "pharmacy window".
    location = fields.CharField(max_length=120, null=True)
    # A counter that has been taken out of use keeps its history instead of being deleted.
    active = fields.BooleanField(default=True)
    # The endpoint that normally rings from here, if the branch wants them bound (X-Device-Id).
    device_id = fields.CharField(max_length=80, null=True)
    sort_order = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "sales_counters"


class CounterDuty(models.Model):
    """One person at one counter, from when they were put there until they left it."""

    id = fields.UUIDField(pk=True)
    counter: fields.ForeignKeyRelation[SalesCounter] = fields.ForeignKeyField(
        "models.SalesCounter", related_name="duties"
    )
    user: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="counter_duties"
    )
    started_at = fields.DatetimeField()
    ended_at = fields.DatetimeField(null=True)
    # Who put them there and who took them off — a cashier opening their own till assigns themselves,
    # and that is recorded as plainly as a manager doing it.
    assigned_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="duties_assigned", null=True, on_delete=fields.SET_NULL
    )
    ended_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="duties_ended", null=True, on_delete=fields.SET_NULL
    )
    note = fields.CharField(max_length=200, null=True)
    device_id = fields.CharField(max_length=80, null=True)

    class Meta:
        table = "counter_duties"
        indexes = (("counter", "started_at"), ("user", "started_at"))
