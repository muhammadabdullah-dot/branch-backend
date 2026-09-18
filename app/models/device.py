"""Endpoint/device identity — architecture doc §4.2: 'a unique identity for every endpoint or
counter that originates transactions.' A device registers itself once (by sending an unseen
X-Device-Id header) and every write's OutboxEvent carries it alongside the acting user.
"""
from tortoise import fields, models


class Device(models.Model):
    id = fields.CharField(max_length=80, pk=True)  # client-generated (e.g. a UUID from the till)
    name = fields.CharField(max_length=120, null=True)
    first_seen_at = fields.DatetimeField(auto_now_add=True)
    last_seen_at = fields.DatetimeField(auto_now=True)
    # Set when a Branch Manager registers this browser by name ("Counter 1 PC") from the device itself.
    # Only a registered device can be assigned to counter staff; every other one is simply seen.
    registered_at = fields.DatetimeField(null=True)
    registered_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="devices_registered", null=True, on_delete=fields.SET_NULL
    )

    class Meta:
        table = "devices"
