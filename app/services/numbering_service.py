"""Where each document series starts: GRN-0001, PR-0001, PO-0001, GG-RQ-0001, GG-TR-0001, GG-PV-000001.

A series keeps its counter row (models/sequence.py), moved on in the same transaction as the document it numbers, so two
documents made at the same moment never share a number. This decides the number a series hands out:

- no counter row yet: one past the highest number the documents of that series already carry, so 1 on an empty system;
- a counter row: its value, unless a document already carries that number or a higher one (the counter fell behind, say
  after documents were copied in from elsewhere), and then one past the highest there is.

No series is primed with a starting number any more (the branch GRNs used to start at GRN-0011, after the old demo
data). A number is never handed out twice, and nothing already numbered changes. Deliberately duplicated from head
office (contracts.md §1: no shared runtime package).

A series may change how many digits it pads to (the branch's Invoice numbers setting lets a Branch Manager pick 4 to 8):
its numbers are compared as numbers, so GG-2026-000143 is still below GG-2026-00000144.
"""
import re

from tortoise.expressions import Q
from tortoise.functions import Length
from tortoise.models import Model

from app.models import Counter


async def highest(model: type[Model], field: str, prefix: str, **filters) -> int:
    """The highest number after `prefix` on any document of `model` (0 when there is none). Only numbers written exactly
    as the series writes them count: with prefix "GRN-", "GRN-0011" is 11 but "GRN-0011-A" is not."""
    pattern = re.compile(re.escape(prefix) + r"(\d+)")
    top = 0
    for value in await model.filter(**{f"{field}__startswith": prefix}, **filters).values_list(field, flat=True):
        match = pattern.fullmatch(value or "")
        if match:
            top = max(top, int(match.group(1)))
    return top


# The longest running number a series is taken to reach: a value after the prefix with more characters than this (say
# GG-2026-000001 seen from the series GG-) is some other series' number, never this one's.
MOST_DIGITS = 9


async def _taken_from(model: type[Model], field: str, number: int, prefix: str, **filters) -> bool:
    """Whether a document of the series already carries `number` or one after it, whatever width it was padded to. At
    one width a later number is as long and later in the alphabet; a number padded to fewer digits is compared at its own
    width (000150 is after 00000144). Values too long to be this series' numbers don't count."""
    shortest = len(str(number))
    later = [
        Q(Q(size=len(prefix) + width), Q(**{f"{field}__gte": f"{prefix}{number:0{width}d}"}))
        for width in range(shortest, max(MOST_DIGITS, shortest) + 1)
    ]
    return await (
        model.filter(**{f"{field}__startswith": prefix}, **filters).annotate(size=Length(field))
        .filter(Q(*later, join_type=Q.OR))
        .exists()
    )


async def _choose(series: str, model: type[Model], field: str, prefix: str, **filters) -> tuple[int, Counter | None]:
    """The number `series` hands out next, and its counter row (None before the series has one)."""
    counter = await Counter.get_or_none(id=series)
    if counter is None:
        return await highest(model, field, prefix, **filters) + 1, None
    number = max(counter.value, 1)
    if await _taken_from(model, field, number, prefix, **filters):
        number = max(number, await highest(model, field, prefix, **filters) + 1)
    return number, counter


async def peek_number(series: str, model: type[Model], field: str, prefix: str, **filters) -> int:
    """The running number next_number would hand out now, without taking it (another till may take it first)."""
    number, _ = await _choose(series, model, field, prefix, **filters)
    return number


async def next_number(series: str, model: type[Model], field: str, prefix: str, width: int, **filters) -> str:
    """The next document number of `series`: `prefix` and the number padded to `width` digits ("TR-0001"), for a `model`
    row that keeps it in `field`. Call it inside the caller's own transaction, so the number and the document that takes
    it commit or fail together."""
    number, counter = await _choose(series, model, field, prefix, **filters)
    if counter is None:
        await Counter.create(id=series, value=number + 1)
    else:
        counter.value = number + 1
        await counter.save(update_fields=["value"])
    return f"{prefix}{number:0{width}d}"
