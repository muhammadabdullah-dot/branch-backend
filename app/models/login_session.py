"""Who is signed in, on which device, and how each login ended.

Every sign-in is a row here, and the token handed out at sign-in names its row. A request whose login has
ended (signed out, ended by a manager, left unused) is refused, so ending a login takes effect on that
person's very next request.

Counter staff get one login at a time, and only on the devices assigned to them (UserDevice below). The rules
live in services/login_session_service.py.
"""
from tortoise import fields, models


class LoginSession(models.Model):
    id = fields.UUIDField(pk=True)
    user: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField("models.User", related_name="login_sessions")
    # The browser they signed in from (X-Device-Id), and its name then, so the record reads right after a rename.
    device_id = fields.CharField(max_length=80, null=True)
    device_name = fields.CharField(max_length=120, null=True)
    ip = fields.CharField(max_length=60, null=True)
    # Whether the counter staff rule (one login, on their own device) applied to this login.
    one_login = fields.BooleanField(default=False)
    started_at = fields.DatetimeField()
    last_seen_at = fields.DatetimeField()
    # When the token handed out at sign-in stops working.
    expires_at = fields.DatetimeField()
    ended_at = fields.DatetimeField(null=True, index=True)
    ended_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="login_sessions_ended", null=True, on_delete=fields.SET_NULL
    )
    # signed-out, ended, same-device, expired, idle, switched-off
    end_kind = fields.CharField(max_length=20, null=True)
    # In words: what a manager typed when ending it, or what happened.
    end_reason = fields.CharField(max_length=200, null=True)

    class Meta:
        table = "login_sessions"
        indexes = (("user", "ended_at"),)


class UserDevice(models.Model):
    """A device one person may sign in on. Only read for people under the counter staff rule."""

    id = fields.UUIDField(pk=True)
    user: fields.ForeignKeyRelation["User"] = fields.ForeignKeyField("models.User", related_name="sign_in_devices")
    device: fields.ForeignKeyRelation["Device"] = fields.ForeignKeyField("models.Device", related_name="people")
    assigned_at = fields.DatetimeField(auto_now_add=True)
    assigned_by: fields.ForeignKeyNullableRelation["User"] = fields.ForeignKeyField(
        "models.User", related_name="sign_in_devices_assigned", null=True, on_delete=fields.SET_NULL
    )

    class Meta:
        table = "user_devices"
        unique_together = (("user", "device"),)
