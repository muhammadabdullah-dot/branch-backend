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

    class Meta:
        table = "devices"
