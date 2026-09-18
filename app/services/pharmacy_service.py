"""Which Items a person sells at the till: decided by how they started, both ways.

Which departments count as Pharmacy is a branch setting (Branch Console > Lists and Settings > Pharmacy), PHARMACY
until the branch says otherwise. A Pharmacist sells Pharmacy Items only, a Salesperson every other Item, a Branch
Manager both (`side`, `may_sell_item`). Billing doesn't offer anyone the other side's Items (their codes read as not
found), and POST /sales refuses them from anyone who may not sell them.

A bill can still be finished by the other side. When a bill is held, every line the person holding it may sell is
marked cleared on the held bill. Recalling it hands someone who may not sell some of those lines a pass: signed by this
server, for that person and that one bill (its POST /sales idempotency key), naming those Items and quantities. The sale
accepts them up to what the pass names, so they can take payment, or take a line off, but not add those Items or raise
their quantities. Holding the bill again with the pass keeps its lines cleared.

A Pharmacist's own bill reaches the cash counter as a pharmacy slip (services/slips_service.py), and there it is only an
amount to collect: its payment is a small sale of its own, and the person taking the money never sees its Items.

Nor anywhere else. Someone who doesn't sell Pharmacy Items is never shown them on a bill: a receipt, a reprint, the
Returns lookup, the sales list, a return receipt, a bill opened up from the dashboard. Every bill's Pharmacy Items reach
them folded into one line, "Pharmacy slip P-0042 · 3 items · Rs 743" (`fold_sale_lines`, `fold_rows`). They don't take
those Items back either, and a held bill carrying any isn't on their list: both are the Branch Manager's.

Bills held before this carry the old marker, `pharmacyCleared`, on their pharmacy lines only: their other lines were
anyone's to sell then, so they count as cleared.
"""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import jwt

from app.core.abilities import BRANCH_MANAGER, PHARMACIST
from app.core.config import settings
from app.models import Product, ShopSetting, User

SETTING_KEY = "pharmacy"
DEFAULT_DEPARTMENTS = ["PHARMACY"]
PASS_AUDIENCE = "dmarina:pharmacy-pass"
# A recalled bill can sit on the till a while (a customer fetching one more thing, a refresh); a day at most.
PASS_TTL = timedelta(hours=12)
ZERO = Decimal("0")
# Pieces are compared to three places, so lines of a few pieces each add up exactly.
_SLACK = Decimal("0.0005")

# Which Items a person sells at the till.
REGULAR, PHARMACY, BOTH = "regular", "pharmacy", "both"
# On a held bill: whether the line was the holder's to sell, or covered by their pass.
CLEARED = "cleared"
_OLD_CLEARED = "pharmacyCleared"


class PharmacyError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


def _key() -> bytes:
    """Its own key, derived from the JWT secret, so a pass can never pass for a sign-in token or a discount approval."""
    return hmac.new(settings.jwt_secret.encode(), b"dmarina/pharmacy-pass", hashlib.sha256).digest()


def _norm(name: str | None) -> str:
    return (name or "").strip().upper()


async def setting() -> tuple[list[str], ShopSetting | None]:
    row = await ShopSetting.get_or_none(key=SETTING_KEY)
    if row and isinstance(row.value, dict) and isinstance(row.value.get("departments"), list):
        return [d for d in (str(x).strip() for x in row.value["departments"]) if d], row
    return list(DEFAULT_DEPARTMENTS), row


async def save_setting(departments: list[str], user: User) -> tuple[list[str], ShopSetting]:
    clean: list[str] = []
    for name in departments:
        name = (name or "").strip()
        if name and _norm(name) not in {_norm(d) for d in clean}:
            clean.append(name[:80])
    now = datetime.now(timezone.utc)
    row = await ShopSetting.get_or_none(key=SETTING_KEY)
    if row is None:
        row = await ShopSetting.create(key=SETTING_KEY, value={"departments": clean}, updated_at=now, updated_by_name=user.name)
    else:
        row.value, row.updated_at, row.updated_by_name = {"departments": clean}, now, user.name
        await row.save()
    return clean, row


async def departments() -> set[str]:
    return {_norm(d) for d in (await setting())[0]}


def is_pharmacy(product: Product, pharmacy_departments: set[str]) -> bool:
    return bool(product.department) and _norm(product.department) in pharmacy_departments


def side(user: User) -> str:
    """Which Items this person sells at the till: a Pharmacist Pharmacy Items only, a Branch Manager both, anyone else
    every Item but those."""
    if user.role_id == BRANCH_MANAGER:
        return BOTH
    if user.role_id == PHARMACIST:
        return PHARMACY
    return REGULAR


def may_sell_item(user: User, product: Product, pharmacy_departments: set[str]) -> bool:
    mine = side(user)
    return mine == BOTH or (mine == PHARMACY) == is_pharmacy(product, pharmacy_departments)


# ── Pharmacy Items out of sight ───────────────────────────────────────────────────────────────────

# The one line a bill's Pharmacy Items fold into, for someone who doesn't sell them.
FOLDED_ID = "pharmacy-slip"


def hides_pharmacy(user: User | None) -> bool:
    """True for someone who is never shown Pharmacy Items on a bill: a Salesperson."""
    return user is not None and side(user) == REGULAR


def slip_numbers(slips) -> list[str]:
    return [str(s["number"]) for s in (slips or []) if isinstance(s, dict) and s.get("number")]


def folded_name(slips, count: int, amount: Decimal) -> str:
    """Pharmacy slip P-0042 · 3 items · Rs 743. A bill that paid no slip (a Branch Manager's own sale) says Pharmacy Items."""
    from app.services.sale_rules import rs

    numbers = slip_numbers(slips)
    what = (f"Pharmacy slip {numbers[0]}" if len(numbers) == 1 else f"Pharmacy slips {', '.join(numbers)}") if numbers else "Pharmacy Items"
    return f"{what} · {count} item{'' if count == 1 else 's'} · {rs(Decimal(amount).quantize(Decimal('1'), rounding=ROUND_HALF_UP))}"


def _line_value(line) -> Decimal:
    """A sale line's worth before discount: a pack or box line is so many packs or boxes at their price."""
    if line.sell_level and line.level_qty is not None and line.level_price is not None:
        return Decimal(line.level_qty) * Decimal(line.level_price)
    return Decimal(line.qty) * Decimal(line.unit_price)


def fold_sale_lines(sale, lines: list, pharmacy_departments: set[str]) -> list:
    """A bill's lines as someone who doesn't sell Pharmacy Items sees them. `lines` are its SaleLineOut, in the order of
    `sale.lines` (their products fetched). Its Pharmacy Items become one line where the first of them was, one for what
    it sold and one for what it took back, at what they came to with their discounts and GST."""
    from app.schemas.sales import SaleLineOut

    out: list = []
    places: dict[bool, int] = {}
    groups: dict[bool, list] = {False: [], True: []}
    for model, line in zip(sale.lines, lines):
        if not is_pharmacy(model.product, pharmacy_departments):
            out.append(line)
            continue
        if model.is_return not in places:
            places[model.is_return] = len(out)
            out.append(None)
        groups[model.is_return].append(model)
    # A slip's own payment is nothing but the slip: it comes to exactly what the bill did.
    whole = not any(line is not None for line in out) and True not in places
    for is_return, place in places.items():
        rows = groups[is_return]
        gross = sum((_line_value(r) for r in rows), ZERO)
        disc = sum((Decimal(r.disc_amount or 0) for r in rows), ZERO)
        worth = Decimal(sale.net_value) if whole else gross - disc + sum((Decimal(r.tax_amount or 0) for r in rows), ZERO)
        out[place] = SaleLineOut(
            productId=FOLDED_ID, name=folded_name(sale.slips, len(rows), worth), sku=", ".join(slip_numbers(sale.slips)),
            qty=Decimal("1"), unitPrice=gross, isWeighed=False, isReturn=is_return, discAmount=disc, folded=True,
        )
    return out


def fold_rows(rows: list[dict], pharmacy_ids: set[str], slips) -> list[dict]:
    """The same for a return receipt's rows ({productId, name, sku, qty, unitPrice, value, ...}): the rows of Pharmacy
    Items become one, at their value."""
    out: list = []
    folded: list[dict] = []
    place = None
    for row in rows:
        if str(row.get("productId")) not in pharmacy_ids:
            out.append(row)
            continue
        if place is None:
            place = len(out)
            out.append(None)
        folded.append(row)
    if place is not None:
        value = sum((Decimal(str(r.get("value") or 0)) for r in folded), ZERO)
        out[place] = {
            "productId": FOLDED_ID, "name": folded_name(slips, len(folded), value), "sku": ", ".join(slip_numbers(slips)),
            "qty": Decimal("1"), "unitPrice": value, "value": value, "isWeighed": False,
        }
    return out


async def pharmacy_ids(product_ids) -> set[str]:
    """Which of these Items are Pharmacy Items."""
    ids = list({str(i) for i in product_ids})
    if not ids:
        return set()
    pharmacy_departments = await departments()
    return {p.id for p in await Product.filter(id__in=ids) if is_pharmacy(p, pharmacy_departments)}


async def carries_pharmacy(lines: list[dict]) -> bool:
    """A held bill with any Pharmacy Item on it."""
    return bool(await pharmacy_ids(line.get("productId") for line in lines or []))


def _key_of(product_id: str, is_return: bool) -> str:
    return f"{'R' if is_return else 'S'}:{product_id}"


async def _products(lines: list[dict]) -> dict[str, Product]:
    ids = list({str(line.get("productId")) for line in lines})
    return {p.id: p for p in await Product.filter(id__in=ids)} if ids else {}


def issue_pass(user: User, bill_id: str, lines: dict[str, Decimal]) -> str:
    """`lines` is {"S:<productId>" or "R:<productId>": pieces}: the cleared lines of the recalled bill this person may
    not sell themselves."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {
        "requester": str(user.id), "bill": bill_id, "lines": {k: format(v, "f") for k, v in lines.items()},
        "aud": PASS_AUDIENCE, "iat": now, "exp": now + PASS_TTL,
    }
    return jwt.encode(payload, _key(), algorithm="HS256")


def verify_pass(token: str | None, user: User, bill_id: str | None, check_bill: bool = True) -> dict[str, Decimal]:
    """What the pass lets this person put on this bill, {"S:<id>" / "R:<id>": pieces}; nothing when there is no pass.
    Its messages don't say which Items or why: only that the bill has to be rung up again."""
    if not token:
        return {}
    try:
        claims = jwt.decode(token, _key(), algorithms=["HS256"], audience=PASS_AUDIENCE)
    except jwt.ExpiredSignatureError:
        raise PharmacyError("This recalled bill has been open too long. Ask whoever held it to ring its Items up again.")
    except jwt.PyJWTError:
        raise PharmacyError("This recalled bill can't be checked. Ask whoever held it to ring its Items up again.")
    if str(claims.get("requester")) != str(user.id):
        raise PharmacyError("This bill was recalled by someone else. Ask whoever held it to ring its Items up again.")
    if check_bill and (not bill_id or str(claims.get("bill")) != bill_id):
        raise PharmacyError("These lines were cleared for a different bill. Ask whoever held it to ring its Items up again.")
    try:
        return {str(k): Decimal(str(v)) for k, v in (claims.get("lines") or {}).items()}
    except (InvalidOperation, AttributeError):
        raise PharmacyError("This recalled bill can't be checked. Ask whoever held it to ring its Items up again.")


def over_pass(lines, products: dict[str, Product], allowed: dict[str, Decimal], sellable) -> str | None:
    """The first Item on these lines this person may not sell (`sellable(product)` is False) beyond what `allowed`
    covers, by name; None when every one is covered. `lines` carry productId, qty (pieces) and isReturn."""
    wanted: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    for line in lines:
        product = products.get(line.productId)
        if not product or sellable(product):
            continue
        key = _key_of(line.productId, line.isReturn)
        wanted[key] = wanted.get(key, ZERO) + Decimal(str(line.qty))
        names[key] = product.name
    for key, qty in wanted.items():
        if qty > allowed.get(key, ZERO) + _SLACK:
            return names[key]
    return None


async def cleared_lines(lines: list[dict], user: User) -> dict[str, Decimal]:
    """What a pass for this person carries from a held bill: its cleared lines they may not sell themselves. Nothing for
    a Branch Manager, who sells both."""
    if side(user) == BOTH:
        return {}
    products = await _products(lines)
    pharmacy_departments = await departments()
    out: dict[str, Decimal] = {}
    for line in lines:
        product = products.get(str(line.get("productId")))
        if not product or may_sell_item(user, product, pharmacy_departments):
            continue
        if CLEARED in line:
            cleared = bool(line[CLEARED])
        else:
            # Held before sides: a pharmacy line carries the old marker, and every other line was anyone's to sell.
            cleared = bool(line.get(_OLD_CLEARED)) or not is_pharmacy(product, pharmacy_departments)
        if cleared:
            key = _key_of(str(line.get("productId")), bool(line.get("isReturn")))
            out[key] = out.get(key, ZERO) + Decimal(str(line.get("qty") or 0))
    return out


async def mark_held_lines(lines: list[dict], user: User, token: str | None, bill_id: str | None) -> list[dict]:
    """Marks every line of a bill being held as cleared, or not: cleared when the person holding it may sell that Item,
    or when their pass for the bill covers it. Whatever the till sent about it is ignored."""
    for line in lines:
        line.pop(_OLD_CLEARED, None)
        line.pop(CLEARED, None)
    products = await _products(lines)
    pharmacy_departments = await departments()
    allowed: dict[str, Decimal] | None = None
    for line in lines:
        product = products.get(str(line.get("productId")))
        if product is None:
            line[CLEARED] = False
            continue
        if may_sell_item(user, product, pharmacy_departments):
            line[CLEARED] = True
            continue
        if allowed is None:
            try:
                allowed = verify_pass(token, user, bill_id)
            except PharmacyError:
                allowed = {}
        key = _key_of(str(line.get("productId")), bool(line.get("isReturn")))
        qty = Decimal(str(line.get("qty") or 0))
        if allowed.get(key, ZERO) + _SLACK >= qty:
            allowed[key] = allowed.get(key, ZERO) - qty
            line[CLEARED] = True
        else:
            line[CLEARED] = False
    return lines
