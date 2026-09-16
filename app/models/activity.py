"""Everything anyone does on this branch server, and who did it.

One row per action that changes something (a sale, a price, a count, a till close, an access change, a
sign-in) or takes data away (an export) — for the Branch Manager exactly as for everyone else. Each row
also goes to head office, so every activity at every branch can be traced to a person there.
"""
from tortoise import fields, models


class ActivityLog(models.Model):
    id = fields.UUIDField(pk=True)
    at = fields.DatetimeField(index=True)
    user: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="activities", null=True, on_delete=fields.SET_NULL
    )
    # Kept as they were at the time, so the record reads right after a rename or with the account gone.
    user_name = fields.CharField(max_length=120, null=True)
    user_title = fields.CharField(max_length=80, null=True)
    # "Create sale", "Approve count", "Sign in"
    action = fields.CharField(max_length=120)
    method = fields.CharField(max_length=10)
    # The route as written (/transfers/{transfer_id}/receive) and the one called.
    route = fields.CharField(max_length=160, null=True)
    path = fields.CharField(max_length=255)
    params = fields.JSONField(null=True)
    status_code = fields.IntField()
    # What was sent, with passwords and secrets taken out, cut short when long.
    detail = fields.JSONField(null=True)
    device_id = fields.CharField(max_length=80, null=True)
    ip = fields.CharField(max_length=60, null=True)

    class Meta:
        table = "activity_logs"
