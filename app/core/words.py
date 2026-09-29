"""A sum of money in words, the way a cheque is written in Pakistan.

On the server rather than in the browser, because a cheque, a receipt and a report must never write the same amount
three different ways, and because the words on a cheque are the amount: a bank pays the words when the figures
disagree with them.

Pakistani, not international: lakh and crore, not million and billion. One hundred thousand rupees is written
"Rupees One Lakh Only", and ten million is "Rupees One Crore Only". Paisa are written out where there are any, and
left off entirely where there are none, which is how a cheque is written.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

ONES = ("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
        "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen")
TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")
# Each step is what the group before it is worth, in the order a Pakistani number is read.
GROUPS = ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand"), (100, "Hundred"))


def _under_hundred(value: int) -> list[str]:
    if value < 20:
        return [ONES[value]] if value else []
    out = [TENS[value // 10]]
    if value % 10:
        out.append(ONES[value % 10])
    return out


def _whole(value: int) -> list[str]:
    """A whole number in words. Crore and lakh take a number of their own, which is itself read this way, so a hundred
    and twenty three crore reads as "One Hundred Twenty Three Crore"."""
    if value == 0:
        return []
    out: list[str] = []
    for size, name in GROUPS:
        if value >= size:
            out += _whole(value // size) + [name]
            value %= size
    return out + _under_hundred(value)


def amount_in_words(amount, currency: str = "Rupees", paisa_word: str = "Paisa") -> str:
    """`Rupees One Lakh Fifty Thousand Only`, or with paisa, `... and Fifty Paisa Only`.

    A negative amount is written as its own size preceded by Minus, because a cheque for a negative amount is a
    mistake somebody should be able to read rather than a blank."""
    value = Decimal(str(amount or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "Minus " if value < 0 else ""
    value = abs(value)
    rupees = int(value)
    paisa = int((value - rupees) * 100)
    words = _whole(rupees)
    if not words:
        words = ["Zero"]
    out = f"{sign}{currency} {' '.join(words)}"
    if paisa:
        out += f" and {' '.join(_whole(paisa))} {paisa_word}"
    return f"{out} Only"
