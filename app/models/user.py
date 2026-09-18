from tortoise import fields, models


class User(models.Model):
    id = fields.UUIDField(pk=True)
    name = fields.CharField(max_length=120)
    email = fields.CharField(max_length=180, unique=True)
    password_hash = fields.CharField(max_length=255)
    role: fields.ForeignKeyRelation["Role"] = fields.ForeignKeyField(
        "models.Role", related_name="users"
    )
    active = fields.BooleanField(default=True)
    # What the person does here, in the branch's own words ("Sales Manager", "Stock Keeper"). Their access
    # is whatever is ticked on the account; the title is for people reading the staff list.
    title = fields.CharField(max_length=80, null=True)
    # The most bill discount they may give without someone else approving it, and the most they may approve
    # for others. Null means the starting point's (Salesperson 5%, Pharmacist 0%, Branch Manager 100%).
    discount_limit = fields.DecimalField(max_digits=5, decimal_places=2, null=True)
    # Counter staff rule: one login at a time, and only on the devices assigned to them. Null follows how they
    # started: on for a Salesperson who rings up sales or a Pharmacist who makes slips, off for everyone else. Kept at
    # this branch only.
    counter_login = fields.BooleanField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    # Raised by one on every change to the account here or at head office. The higher revision wins
    # wherever it arrives; see services/staff_sync_service.py.
    rev = fields.IntField(default=1)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "users"
