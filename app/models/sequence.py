"""Server-side sequence generation (contracts.md §6) — a DB row incremented inside the same
transaction as the record that consumes it, replacing the frontend's in-memory counter."""
from tortoise import fields, models
from tortoise.transactions import in_transaction


class Counter(models.Model):
    id = fields.CharField(max_length=60, pk=True)
    value = fields.IntField()

    class Meta:
        table = "counters"


async def next_value(name: str, seed: int) -> int:
    """Returns the next number to use and persists the increment. Call inside the caller's own
    transaction so the sequence advance and the record it numbers commit or fail together."""
    counter = await Counter.get_or_none(id=name)
    if counter is None:
        await Counter.create(id=name, value=seed + 1)
        return seed
    current = counter.value
    counter.value = current + 1
    await counter.save()
    return current
