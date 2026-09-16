from tortoise import fields, models


class Role(models.Model):
    id = fields.CharField(max_length=40, pk=True)
    name = fields.CharField(max_length=80)
    landing = fields.CharField(max_length=120)
    # Set once head office has taken over this role's standard access. From then on the branch stops
    # adding the software's own defaults back at startup — head office's list is the list.
    managed_by_head_office = fields.BooleanField(default=False)
    # The software's standard access for this role as last handed out to its people at startup. Only
    # what's been added since goes out next time, so a screen someone deliberately took away stays away.
    rolled_out_resources = fields.JSONField(null=True)

    class Meta:
        table = "roles"


class RoleDefaultPermission(models.Model):
    """Seed-time template only — copied onto a user at creation, never read at request time."""

    id = fields.UUIDField(pk=True)
    role: fields.ForeignKeyRelation[Role] = fields.ForeignKeyField(
        "models.Role", related_name="default_permissions"
    )
    resource = fields.CharField(max_length=120)
    can_read = fields.BooleanField(default=False)
    can_write = fields.BooleanField(default=False)
    can_execute = fields.BooleanField(default=False)

    class Meta:
        table = "role_default_permissions"
        unique_together = (("role", "resource"),)
