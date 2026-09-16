"""OutboxEvent — contracts.md §3.5. Written atomically alongside every branch-owned business write.
Nothing consumes it yet (I7 builds the sync worker) — I3 onward is responsible for writing it correctly."""
from tortoise import fields, models


class OutboxEvent(models.Model):
    id = fields.UUIDField(pk=True)
    aggregate_type = fields.CharField(max_length=60)
    aggregate_id = fields.CharField(max_length=60)
    payload = fields.JSONField()
    origin_user_id = fields.CharField(max_length=60, null=True)
    origin_device_id = fields.CharField(max_length=80, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    status = fields.CharField(max_length=20, default="pending")
    attempt_count = fields.IntField(default=0)
    last_attempt_at = fields.DatetimeField(null=True)
    last_error = fields.TextField(null=True)

    class Meta:
        table = "outbox_events"
        # The sync loop asks the same question every tick — the oldest events still waiting — and this
        # table only ever grows, so it is asked of an index rather than of every row.
        indexes = (("status", "created_at"),)
