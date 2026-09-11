from tortoise import fields, models


class Location(models.Model):
    id = fields.CharField(max_length=40, pk=True)
    name = fields.CharField(max_length=80)
    kind = fields.CharField(max_length=20)
    priority = fields.IntField(default=1)

    class Meta:
        table = "locations"
