"""Pharmacy Items are sold only by the people chosen for it.

Which departments count as Pharmacy is a branch setting (Branch Console > Lists and Settings > Pharmacy), PHARMACY
until the branch says otherwise. Only someone with "Sell Pharmacy Items" puts those Items on a bill: Billing doesn't
offer them to anyone else, and POST /sales refuses them from anyone else.

A cashier without the tick can still finish a bill pharmacy staff started. When pharmacy staff hold a bill, its pharmacy
lines are marked cleared on the held bill. Recalling it hands the cashier a pass: signed by this server, for that
cashier and that one bill (its POST /sales idempotency key), naming the pharmacy Items and quantities it carried. The
sale accepts pharmacy lines up to what the pass names, so the cashier can take payment, or take a line off, but not add
pharmacy Items or raise their quantities. Holding the bill again with the pass keeps its lines cleared.
"""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import jwt

from app.core.config import settings
from app.models import Product, ShopSetting, User, UserPermission

SELL_RESOURCE = "store.pharmacy"
SETTING_KEY = "pharmacy"
DEFAULT_DEPARTMENTS = ["PHARMACY"]
PASS_AUDIENCE = "dmarina:pharmacy-pass"
# A recalled bill can sit on the till a while (a customer fetching one more thing, a refresh); a day at most.
PASS_TTL = timedelta(hours=12)
ZERO = Decimal("0")


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


async def may_sell(user: User) -> bool:
    return await UserPermission.filter(user_id=user.id, resource=SELL_RESOURCE, can_execute=True).exists()


def _key_of(product_id: str, is_return: bool) -> str:
    return f"{'R' if is_return else 'S'}:{product_id}"


def issue_pass(user: User, bill_id: str, lines: dict[str, Decimal]) -> str:
    """`lines` is {"S:<productId>" or "R:<productId>": pieces} for the cleared pharmacy lines of the recalled bill."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {
        "requester": str(user.id), "bill": bill_id, "lines": {k: format(v, "f") for k, v in lines.items()},
        "aud": PASS_AUDIENCE, "iat": now, "exp": now + PASS_TTL,
    }
    return jwt.encode(payload, _key(), algorithm="HS256")


def verify_pass(token: str | None, user: User, bill_id: str | None, check_bill: bool = True) -> dict[str, Decimal]:
    """What the pass lets this person put on this bill, {"S:<id>" / "R:<id>": pieces}; nothing when there is no pass."""
    if not token:
        return {}
    try:
        claims = jwt.decode(token, _key(), algorithms=["HS256"], audience=PASS_AUDIENCE)
    except jwt.ExpiredSignatureError:
        raise PharmacyError("This recalled bill's pharmacy lines were cleared too long ago. Ask pharmacy staff to ring them up again.")
    except jwt.PyJWTError:
        raise PharmacyError("This bill's pharmacy lines can't be checked. Ask pharmacy staff to ring them up again.")
    if str(claims.get("requester")) != str(user.id):
        raise PharmacyError("These pharmacy lines were recalled by someone else. Ask pharmacy staff to ring them up again.")
    if check_bill and (not bill_id or str(claims.get("bill")) != bill_id):
        raise PharmacyError("These pharmacy lines were cleared for a different bill. Ask pharmacy staff to ring them up again.")
    try:
        return {str(k): Decimal(str(v)) for k, v in (claims.get("lines") or {}).items()}
    except (InvalidOperation, AttributeError):
        raise PharmacyError("This bill's pharmacy lines can't be checked. Ask pharmacy staff to ring them up again.")


def over_pass(lines, products: dict[str, Product], pharmacy_departments: set[str], allowed: dict[str, Decimal]) -> str | None:
    """The first pharmacy Item on these lines beyond what `allowed` covers, by name; None when every one is covered.
    `lines` carry productId, qty (pieces) and isReturn."""
    wanted: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    for line in lines:
        product = products.get(line.productId)
        if not product or not is_pharmacy(product, pharmacy_departments):
            continue
        key = _key_of(line.productId, line.isReturn)
        wanted[key] = wanted.get(key, ZERO) + Decimal(str(line.qty))
        names[key] = product.name
    for key, qty in wanted.items():
        if qty > allowed.get(key, ZERO) + Decimal("0.0005"):
            return names[key]
    return None


def cleared_lines(lines: list[dict]) -> dict[str, Decimal]:
    """The cleared pharmacy lines of a held bill, as a pass carries them."""
    out: dict[str, Decimal] = {}
    for line in lines:
        if line.get("pharmacyCleared"):
            key = _key_of(str(line.get("productId")), bool(line.get("isReturn")))
            out[key] = out.get(key, ZERO) + Decimal(str(line.get("qty") or 0))
    return out


async def mark_held_lines(lines: list[dict], user: User, token: str | None, bill_id: str | None) -> list[dict]:
    """Marks each pharmacy line of a bill being held as cleared, or not: cleared when the person holding it may sell
    Pharmacy Items, or when their pass for the bill covers it. Whatever the till sent about it is ignored."""
    for line in lines:
        line.pop("pharmacyCleared", None)
    ids = list({str(line.get("productId")) for line in lines})
    products = {p.id: p for p in await Product.filter(id__in=ids)}
    pharmacy_departments = await departments()
    pharmacy = [line for line in lines if products.get(str(line.get("productId"))) and is_pharmacy(products[str(line.get("productId"))], pharmacy_departments)]
    if not pharmacy:
        return lines
    if await may_sell(user):
        for line in pharmacy:
            line["pharmacyCleared"] = True
        return lines
    try:
        allowed = verify_pass(token, user, bill_id)
    except PharmacyError:
        allowed = {}
    for line in pharmacy:
        key = _key_of(str(line.get("productId")), bool(line.get("isReturn")))
        qty = Decimal(str(line.get("qty") or 0))
        if allowed.get(key, ZERO) + Decimal("0.0005") >= qty:
            allowed[key] = allowed.get(key, ZERO) - qty
            line["pharmacyCleared"] = True
    return lines
