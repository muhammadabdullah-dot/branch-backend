"""The centerpiece of I3 (contracts.md §6). POST /sales must be one atomic transaction covering
the sale record, line items, stock movement posting, voucher redemption, and outbox evidence:
@atomic() rolls back all of it on any failure, so a partial sale can never exist.
"""
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from tortoise.transactions import atomic

from app.core.device_context import get_device_id
from app.core.pk_time import now_pk
from app.models import (
    Location,
    OutboxEvent,
    Party,
    Product,
    ProductAlias,
    SaleLine,
    SaleRecord,
    SaleTender,
    StockMovement,
    TillSession,
    User,
)
from app.schemas.sales import SaleCreateRequest
from app.schemas.types import money_str
from app.services import discount_approval_service, gift_voucher_service, media_service, members_service, sale_rules, sell_levels

DISCOUNT_LIMIT_PERCENT = Decimal("5")
# Payments that always need the customer's name and mobile number, and make them a member.
CARD_TENDERS = ("CARD",)
ONLINE_TENDERS = ("EASYPAISA", "JAZZCASH", "BANK")
WALLET_TENDERS = ("EASYPAISA", "JAZZCASH")
PROOF_FOLDER = "payment-proofs"
PROOF_MAX_BYTES = 6 * 1024 * 1024
DEFAULT_LOCATION_ID = "loc-1"
ZERO = Decimal("0")


class SaleError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


# A bill that would sell at a loss is refused whatever approval came with it: nobody can override the floor.
LOSS = 409
# A line at a price that isn't the Item's own (a price changed since it was scanned, or one typed): the till fetches the
# prices again and says what changed.
PRICE_CHANGED = 409


# Until this branch server has been verified it has no identity, and an invoice has to be numbered
# something. `BR` is deliberately not a real branch's code: a bill printed before setup is a bill
# that should be obvious as such, rather than one quietly filed under whichever branch the software
# was written in.
UNVERIFIED_PREFIX = "BR"


async def invoice_prefix() -> str:
    """This branch's own code, from the identity head office issued it (BR before setup).

    It used to be the literal string "HO". That was the head office assumption baked into the
    numbering: every branch in the chain would have printed bills numbered as if they were Head
    Office, and two branches' invoice numbers would have collided the moment a second one opened.
    A branch is a branch: its bills carry its own code.

    It is the branch code, not the bill prefix: members' codes and FBR's test numbers use it as it is. How bills and
    returns are numbered is the Invoice numbers setting (services/invoice_numbers_service.py), whose prefixes start with
    this code.
    """
    from app.services import registration_service

    identity = await registration_service.current()
    return identity.code if identity else UNVERIFIED_PREFIX


async def peek_next_invoice_number() -> str:
    """The number the next bill will most likely get (another till may take it first): GG-2026-000001 on a new branch,
    or whatever the branch's Invoice numbers setting makes it."""
    from app.services import invoice_numbers_service

    return await invoice_numbers_service.peek_bill_number()


async def next_invoice_number() -> str:
    """GG-2026-000001: the bill prefix (the branch's code unless its Branch Manager set another under Invoice numbers),
    the Pakistan year, then the bill's running number, padded to 6 digits unless set otherwise. It starts at 1 on a new
    branch (it used to start at 143, a number left from the demo data) and carries on from the highest bill on a branch
    that has bills; a number already on a bill is never handed out again, and no bill is renumbered. With the year in
    the number each year counts from 1 again. Inside the sale's own transaction (services/invoice_numbers_service.py)."""
    from app.services import invoice_numbers_service

    return await invoice_numbers_service.next_bill_number()


async def _resolve_party(party_id: str | None) -> Party:
    if party_id:
        party = await Party.get_or_none(id=party_id)
        if not party:
            raise SaleError("Party not found")
        return party
    party = await Party.get_or_none(is_walk_in=True)
    if not party:
        raise SaleError("Walk-in party not seeded")
    return party


def save_payment_proof(content: bytes) -> str:
    """Keep a customer's transfer screenshot and return the id the sale will refer to."""
    stamp = now_pk().strftime("%Y%m%d")
    return media_service.save_picture(PROOF_FOLDER, stamp, content, max_bytes=PROOF_MAX_BYTES)


async def payment_proof(invoice_number: str, code: str):
    tender = await SaleTender.get_or_none(
        sale__invoice_number=invoice_number.strip().upper(), code=code.strip().upper(), proof__not_isnull=True,
    )
    return media_service.picture_file(tender.proof) if tender else None


async def _resolve_member(payload: SaleCreateRequest, party: Party, cashier: User):
    """The member this bill is for, or None. Card and online payments sign the customer up if they're
    new; so does a customer paying another way who chose to join."""
    info = payload.member
    card = [c for c in CARD_TENDERS if payload.tenders.get(c, ZERO) > 0]
    online = [c for c in ONLINE_TENDERS if payload.tenders.get(c, ZERO) > 0]
    needs_member = bool(card or online)
    try:
        if info and info.code:
            member = await members_service.get(info.code)
            if not member.active:
                raise SaleError(f"Member {member.code} ({member.name}) is switched off.")
            if party and not party.is_walk_in and member.party_id is None:
                member, _ = await members_service.find_or_create(
                    member.name, member.phone, joined_via=member.joined_via, joined_method=member.joined_method,
                    user=cashier, party=party,
                )
            return member
        if info and (info.phone or info.name) and (needs_member or info.join):
            if not (info.name or "").strip() or not (info.phone or "").strip():
                raise SaleError(
                    "Card and online payments need the customer's name and mobile number." if needs_member
                    else "Enter the customer's name and mobile number to make them a member."
                )
            member, _ = await members_service.find_or_create(
                info.name, info.phone,
                joined_via="card" if card else "online" if online else "till",
                joined_method=(card or online or [None])[0], user=cashier, party=party,
            )
            return member
    except members_service.MemberError as exc:
        raise SaleError(exc.message) from exc
    if needs_member:
        raise SaleError("Card and online payments need the customer's name and mobile number.")
    return None


async def _tender_details(payload: SaleCreateRequest) -> dict[str, dict]:
    """Checks what each card and online payment has to carry, and returns it ready to store."""
    out: dict[str, dict] = {}
    details = payload.tenderDetails or {}
    for code in CARD_TENDERS + ONLINE_TENDERS:
        if payload.tenders.get(code, ZERO) <= 0:
            continue
        d = details.get(code)
        if code == "CARD":
            last4 = ((d.reference if d else None) or "").strip()
            if not (len(last4) == 4 and last4.isdigit()):
                raise SaleError("Enter the card's last 4 digits from the machine's shop copy.")
            out[code] = {"reference": last4}
        elif code in WALLET_TENDERS:
            name = "Easypaisa" if code == "EASYPAISA" else "JazzCash"
            try:
                number = members_service.normalize_phone(d.reference if d else None, f"the {name} account number")
            except members_service.MemberError as exc:
                raise SaleError(exc.message) from exc
            out[code] = {"reference": number, "transaction_id": ((d.transactionId if d else None) or "").strip() or None}
        else:  # BANK
            account = ((d.account if d else None) or "").strip()
            txn = ((d.transactionId if d else None) or "").strip()
            proof = ((d.proofId if d else None) or "").strip()
            if not account:
                raise SaleError("Pick the bank account the transfer went into.")
            if not txn:
                raise SaleError("Enter the bank transfer's transaction ID from the customer's screenshot.")
            used = await SaleTender.filter(code="BANK", transaction_id__iexact=txn).prefetch_related("sale").first()
            if used:
                raise SaleError(f"Transaction ID {txn} already paid bill {used.sale.invoice_number}. Check the customer's screenshot.")
            if not proof or not proof.startswith(f"{PROOF_FOLDER}/") or ".." in proof or not media_service.picture_file(proof):
                raise SaleError("Attach the customer's screenshot of the bank transfer.")
            out[code] = {"transaction_id": txn, "account": account[:80], "proof": proof}
    return out


def _line_gross(line, unit_price: Decimal) -> Decimal:
    """What the line is worth before any discount, signed. A pack or box line is so many packs or boxes at their price."""
    sign = Decimal("-1") if line.isReturn else Decimal("1")
    if getattr(line, "level", None):
        return sign * line.levelQty * line.levelPrice
    return sign * line.qty * unit_price


CENT = Decimal("0.01")


def _cents(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _own_prices(line, product: Product, wholesale_pct: Decimal) -> dict[str, Decimal | None]:
    """The Item's own prices for what this line sells: {"retail": its sale price, "wholesale": its wholesale price (its
    own, else the sale price less the branch's wholesale discount, to the rupee, as the till works it out)}. A line sold
    loose or together is priced by the piece, strip, pack or box (services/sell_levels.py)."""
    wholesale = Decimal(product.wholesale_price) if product.wholesale_price is not None else (
        Decimal(product.price) * (Decimal("100") - wholesale_pct) / Decimal("100")
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if line.level:
        return {"retail": sell_levels.own_price(product, line.level), "wholesale": sell_levels.wholesale_price(product, line.level, wholesale)}
    return {"retail": Decimal(product.price), "wholesale": wholesale}


def _line_price(line) -> Decimal:
    """The price the line was rung at: of one piece, strip, pack or box for a line sold that way, else of one unit."""
    return Decimal(line.levelPrice) if line.level else Decimal(line.unitPrice)


def _own_price(line, product: Product, wholesale_pct: Decimal) -> bool:
    """Is this line at one of the Item's own prices: its sale price, or its wholesale price? An Item already priced below
    cost still sells at its own price; any other price below cost is refused."""
    return _cents(_line_price(line)) in {_cents(p) for p in _own_prices(line, product, wholesale_pct).values() if p is not None}


def _check_price(line, product: Product, tier: str | None, wholesale_pct: Decimal, slip: dict | None) -> None:
    """A line sold on a bill is at the Item's own price for the bill: its sale price on a retail bill, its wholesale price
    on a wholesale bill (either, from a till that doesn't say which), by the piece, strip, pack or box when sold that way.
    Whatever comes off it comes through the discounts (the Item's own, the bill's, an approval, the floor): never through a
    lower price. A price the till had from before a change is refused, so the till fetches the price again."""
    prices = _own_prices(line, product, wholesale_pct)
    allowed = [prices[tier]] if tier in prices else list(prices.values())
    if _cents(_line_price(line)) in {_cents(p) for p in allowed if p is not None}:
        return
    if slip is not None:
        # The slip's own words (services/slips_service.py pay): never naming an Item the person paying may not sell.
        raise SaleError(f"Slip {slip.get('number')} was printed before a price changed. Ask the Pharmacist to print it again.", PRICE_CHANGED)
    now = prices[tier] if tier in prices else prices["retail"]
    what = f"a {line.level} of {product.name}" if line.level else product.name
    raise SaleError(
        f"The {'wholesale ' if tier == 'wholesale' else ''}price of {what} changed to {sale_rules.rs(now or ZERO)}. Scan it again.",
        PRICE_CHANGED,
    )


def _units_exactly(product: Product, level: str | None, level_qty, qty) -> Decimal:
    """Stocked units in a line, not rounded to the 0.001 stock keeps (1 tablet of a 30-tablet box is 1/30, not 0.033)."""
    if level in sell_levels.LOOSE and level_qty is not None and sell_levels.pieces_in(product, level):
        return Decimal(level_qty) * sell_levels.pieces_in(product, level) / sell_levels.pieces_per_unit(product)
    if level in sell_levels.TOGETHER and level_qty is not None and sell_levels.units_in(product, level):
        return Decimal(level_qty) * sell_levels.units_in(product, level)
    return Decimal(qty)


async def _paid_on_bill(invoice: str, product: Product, level: str | None) -> tuple[Decimal, Decimal]:
    """What one of what a return line counts (a stocked unit, or one piece, strip, pack or box) cost the customer on a
    bill: its price and the discount taken off it there (the Item's own and its share of the bill's). From the bill's lines
    sold the same way when it has any; otherwise from what a stocked unit cost there. The till works it out the same way
    (pages/store/Billing.tsx paidEach)."""
    sale = await SaleRecord.get(invoice_number=invoice)
    lines = await SaleLine.filter(sale_id=sale.id, product_id=product.id, is_return=False)

    def gross_of(l) -> Decimal:
        if l.sell_level and l.level_qty is not None and l.level_price is not None:
            return Decimal(l.level_qty) * Decimal(l.level_price)
        return Decimal(l.qty) * Decimal(l.unit_price)

    same = [l for l in lines if (l.sell_level or None) == (level or None) and (not level or l.level_qty)]
    if same:
        count = sum((Decimal(l.level_qty) if level else Decimal(l.qty) for l in same), ZERO)
        if count > 0:
            return sum((gross_of(l) for l in same), ZERO) / count, sum((Decimal(l.disc_amount or 0) for l in same), ZERO) / count
    units = sum((_units_exactly(product, l.sell_level, l.level_qty, l.qty) for l in lines), ZERO)
    if units <= 0:
        return ZERO, ZERO
    each = _units_exactly(product, level, 1, 1) if level else Decimal("1")
    return sum((gross_of(l) for l in lines), ZERO) / units * each, sum((Decimal(l.disc_amount or 0) for l in lines), ZERO) / units * each


async def _check_return_price(line, product: Product, invoice: str) -> Decimal:
    """A return line on a bill comes back at what the customer paid for it on the bill it came from, not at today's price:
    its price there, less the discount it had there. Returns that discount for the line (its whole quantity)."""
    price, disc = await _paid_on_bill(invoice, product, line.level)
    # A paisa either way: the till rounds the price to the paisa.
    if abs(_line_price(line) - price) > CENT + Decimal("0.0001"):
        what = line.level or (product.unit or "unit").strip() or "unit"
        raise SaleError(
            f"{product.name} comes back at what it was sold for on bill {invoice}: {sale_rules.rs(price)} a {what}. "
            "Take the line off and scan it again.",
            PRICE_CHANGED,
        )
    count = Decimal(line.levelQty) if line.level and line.levelQty is not None else Decimal(line.qty)
    return (disc * count).quantize(CENT, rounding=ROUND_HALF_UP)


def _item_disc(line, product: Product, alias: ProductAlias | None) -> Decimal:
    """The discount set on the Item (or on the pack barcode it was rung up by), signed like the line."""
    sign = Decimal("-1") if line.isReturn else Decimal("1")
    if alias and alias.qty and alias.qty > 0:
        percent, flat, flat_units = alias.disc_percent, alias.disc_flat, line.qty / alias.qty
    else:
        percent, flat, flat_units = product.disc_percent, product.disc_flat, line.qty
    value = abs(_line_gross(line, line.unitPrice))
    amount = value * (percent or ZERO) / Decimal("100") + flat_units * (flat or ZERO)
    # Never more than the line is worth.
    return sign * min(amount, value)


async def returned_on_bills(sale: SaleRecord, product_id: str) -> Decimal:
    """How much of an Item later bills took back as return lines naming this bill (Billing's return mode), in stocked
    units. The Returns screen counts these as already returned too."""
    rows = await SaleLine.filter(is_return=True, return_of_invoice=sale.invoice_number, product_id=product_id).values_list("qty", flat=True)
    return sum((Decimal(str(q)) for q in rows), ZERO)


async def _check_return_line(line, product: Product, lines, products: dict[str, Product]) -> str:
    """The bill number a return line's goods came from, once it is proven: the bill exists, sold this Item, has this
    much of it left to return (after the Returns screen, earlier bills and this one), and is inside the Item's return
    window. SaleError otherwise, in the words the cashier needs."""
    from app.models import ReturnLine
    from app.services import return_window_service

    invoice = (line.returnOf or "").strip().upper()
    if not invoice:
        raise SaleError(f"{product.name} is coming back: scan or type the number of the bill it was bought on.")
    sale = await SaleRecord.get_or_none(invoice_number=invoice)
    if not sale:
        raise SaleError(f"Bill {invoice} isn't on this branch's records. Check the number on the customer's bill.")
    sold = sum((Decimal(str(q)) for q in await SaleLine.filter(sale_id=sale.id, product_id=product.id, is_return=False).values_list("qty", flat=True)), ZERO)
    if sold <= 0:
        raise SaleError(f"{product.name} isn't on bill {invoice}, so it can't come back against it.")
    try:
        await return_window_service.refuse_outside_window(sale.at, [product], invoice)
    except return_window_service.ReturnWindowError as exc:
        raise SaleError(exc.message) from exc
    already = sum((Decimal(str(q)) for q in await ReturnLine.filter(return_record__against_id=sale.id, product_id=product.id).values_list("qty", flat=True)), ZERO)
    already += await returned_on_bills(sale, product.id)
    on_this_bill = sum(
        (l.qty for l in lines if l.isReturn and l.productId == product.id and (l.returnOf or "").strip().upper() == invoice), ZERO,
    )
    left = sold - already
    if on_this_bill > left + Decimal("0.0005"):
        from app.services import stock_guard

        per_unit = sell_levels.pieces_per_unit(product)

        def say(q: Decimal) -> str:
            return stock_guard.pieces_text(product, sell_levels.pieces_of(q, per_unit)) if per_unit else stock_guard.qty_text(q)

        raise SaleError(
            f"Only {say(max(left, ZERO))} of {product.name} is left to return on bill {invoice} "
            f"({say(already)} already came back of {say(sold)} bought)."
        )
    return invoice


@atomic()
async def create_sale(cashier: User, payload: SaleCreateRequest, *, slip: dict | None = None) -> SaleRecord:
    """`slip` is set when this sale is a pharmacy slip's payment (services/slips_service.py pay): {"number",
    "pharmacistId", "pharmacistName"}. Its lines are the slip's own, every one the Pharmacist's to sell, so whoever takes
    the payment needs no pass for them; and it is a payment only, so no member is signed up and no points are earned."""
    # Idempotent replay: checked FIRST, before any other validation or side effect, so a
    # retried request (lost response, double-click) returns the already-committed sale instead
    # of re-running credit-limit checks, re-posting stock movements, or re-redeeming a voucher.
    # Note: this closes the common case (a delayed/lost-response retry after the first request
    # already committed) but not a true simultaneous double-fire: two requests with the same
    # key landing in the same instant could both pass this check before either commits, and the
    # second would then fail on the column's unique constraint rather than returning the first
    # sale gracefully. Acceptable for now (a single till/cashier submitting sequentially can't
    # produce that race; only a client bug firing the exact same request twice in parallel could).
    if payload.clientRequestId:
        existing = await SaleRecord.get_or_none(client_request_id=payload.clientRequestId)
        if existing:
            await existing.fetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by", "member")
            return existing

    if not payload.lines:
        raise SaleError("A sale needs at least one line")
    # The bill goes into the drawer the person ringing it has open: that is what makes each counter
    # reconcile on its own.
    from app.services import till_service

    till = await till_service.session_for(cashier)
    if not till:
        raise SaleError("No Till is open. Open one at your counter before taking payment")

    # A payment method the branch has switched off (Branch Console > Lists and Settings) can't be taken.
    from app.services import masters_service

    try:
        await masters_service.refuse_switched_off_methods(code for code, amount in payload.tenders.items() if amount > 0)
    except masters_service.MastersError as exc:
        raise SaleError(exc.message) from exc

    party = await _resolve_party(payload.partyId)
    products: dict[str, Product] = {}
    aliases: dict[int, ProductAlias] = {}
    for index, line in enumerate(payload.lines):
        product = await Product.get_or_none(id=line.productId)
        if not product:
            raise SaleError(f"Unknown product {line.productId}")
        products[line.productId] = product
        if line.aliasCode:
            alias = await ProductAlias.get_or_none(code=line.aliasCode.strip(), product_id=product.id)
            if alias:
                aliases[index] = alias

    # A line sold loose (pieces, strips) or together (packs, boxes): the server works out the stocked units it is, which
    # is what stock, cost of sale and the floor count, and the price of one unit, for the reports that count units
    # (services/sell_levels.py). A loose line's pieces are kept aside for moving stock a whole number of pieces at a time.
    loose_pieces: dict[int, int] = {}
    for line in payload.lines:
        if not line.level:
            continue
        product = products[line.productId]
        if not sell_levels.sells_as(product, line.level):
            raise SaleError(f"{product.name} isn't sold by the {line.level}. Take the line off and scan it again.")
        if line.levelQty is None or line.levelQty <= 0 or line.levelQty != line.levelQty.to_integral_value():
            raise SaleError(f"{product.name}: a {line.level} line needs a whole number of {line.level}es." if line.level == "box"
                            else f"{product.name}: a {line.level} line needs a whole number of {line.level}s.")
        if line.levelPrice is None or line.levelPrice < 0:
            raise SaleError(f"{product.name}: the {line.level} price is missing. Take the line off and scan it again.")
        line.qty = sell_levels.units_for(product, line.level, line.levelQty)
        line.unitPrice = sell_levels.unit_price(line.levelQty * line.levelPrice, line.qty)
        if line.level in sell_levels.LOOSE:
            loose_pieces[id(line)] = int(line.levelQty) * sell_levels.pieces_in(product, line.level)

    # A return line names the bill its goods came from: the Item has to be on that bill with that much not taken back
    # already, and still inside its department's return window. Nobody overrides the window at the counter.
    returns_of: dict[int, str] = {}
    for line in payload.lines:
        if line.isReturn:
            returns_of[id(line)] = await _check_return_line(line, products[line.productId], payload.lines, products)

    # Items go on a bill only from people who may sell them: Pharmacy Items from a Pharmacist, every other Item from a
    # Salesperson, both from a Branch Manager. The other side can still take payment for a bill someone held, up to the
    # lines it carried that they may not sell (the pass recalling it handed them). A pharmacy slip's payment carries only
    # the slip's own lines, which the Pharmacist sold.
    from app.services import pharmacy_service

    pharmacy_departments = await pharmacy_service.departments()

    def sellable(product: Product) -> bool:
        return pharmacy_service.may_sell_item(cashier, product, pharmacy_departments)

    if slip is None and not all(sellable(p) for p in products.values()):
        try:
            allowed = pharmacy_service.verify_pass(payload.pharmacyPass, cashier, payload.clientRequestId)
        except pharmacy_service.PharmacyError as exc:
            raise SaleError(exc.message, status=403) from exc
        refused = pharmacy_service.over_pass(payload.lines, products, allowed, sellable)
        if refused:
            # Why these Items can't go on this bill is not for this person to know: the till says only that they can't.
            raise SaleError(f"{refused} can't be sold on this bill. Take it off and try again.", status=403)

    # Every line at the Item's own price for the bill (a price is never a discount), and a line coming back at what was
    # paid for it on the bill it came from, with the discount it had there. A replacement for goods that came back
    # (Returns, Replace) is priced by the server at what was paid, so it is taken as it is.
    wholesale_pct = (await masters_service.pricing_stock())["wholesaleDiscountPercent"]
    return_discs: dict[int, Decimal] = {}
    for line in payload.lines:
        if line.isReturn:
            return_discs[id(line)] = await _check_return_price(line, products[line.productId], returns_of[id(line)])
        elif not sale_rules.replacing.get():
            _check_price(line, products[line.productId], payload.tier, wholesale_pct, slip)

    # Empty means empty: every Item on the bill must be in stock for its whole quantity. What this same bill takes back
    # (an exchange) is back on the shelf first, so it counts.
    from app.services import stock_guard

    wanted: dict[str, Decimal] = {}
    taken_back: dict[str, Decimal] = {}
    for line in payload.lines:
        bucket = taken_back if line.isReturn else wanted
        bucket[line.productId] = bucket.get(line.productId, ZERO) + line.qty
    # Someone allowed to "Sell when stock shows zero" sells what the shelf holds but the records don't yet: what is missing
    # comes off Main Store below zero, and the Item shows on Sold without stock until its delivery is received.
    past_zero = await stock_guard.may_sell_past_zero(cashier)
    if wanted and not past_zero:
        held = await stock_guard.on_hand(list(wanted))
        short = []
        for pid, qty in wanted.items():
            have = held.get(pid, ZERO) + taken_back.get(pid, ZERO)
            per_unit = sell_levels.pieces_per_unit(products[pid])
            if per_unit:
                # An Item sold loose is counted in whole pieces, so lines of a few pieces each add up exactly.
                need = sum(
                    (loose_pieces[id(l)] if id(l) in loose_pieces else sell_levels.pieces_of(l.qty, per_unit))
                    for l in payload.lines if l.productId == pid and not l.isReturn
                )
                back = sum(
                    (loose_pieces[id(l)] if id(l) in loose_pieces else sell_levels.pieces_of(l.qty, per_unit))
                    for l in payload.lines if l.productId == pid and l.isReturn
                )
                have_pieces = sell_levels.pieces_of(held.get(pid, ZERO), per_unit) + back
                if have_pieces < need:
                    short.append(f"{products[pid].name} ({stock_guard.pieces_text(products[pid], max(have_pieces, 0))} in stock, "
                                 f"{stock_guard.pieces_text(products[pid], need)} on the bill)")
                continue
            if have < qty:
                short.append(f"{products[pid].name} ({stock_guard.qty_text(have)} in stock, {stock_guard.qty_text(qty)} on the bill)")
        if short:
            raise SaleError("Not enough stock: " + "; ".join(short) + ". Lower the quantity or take the Item off the bill.")

    gross = sum((_line_gross(l, l.unitPrice) for l in payload.lines), ZERO)
    # The Item's own discount comes first and needs no one's approval: it's set on the Item. A line
    # rung up by a pack barcode takes that pack's discount instead (flat is per pack).
    # A line coming back takes back the discount it had on its bill, not the Item's discount today.
    raw_item_discs = [
        -return_discs[id(l)] if l.isReturn else _item_disc(l, products[l.productId], aliases.get(i)) for i, l in enumerate(payload.lines)
    ]
    # No discount sells at a loss (services/sale_rules.py): the Item's own discount stops at its floor, an Item already
    # priced at or below cost takes none, and a bill discount (never on Lock Discount Items or on what the bill takes
    # back) is shared by the profit each line has left.
    bill = sale_rules.figures(
        [
            sale_rules.LineIn(
                name=products[l.productId].name, is_return=l.isReturn, gross=abs(_line_gross(l, l.unitPrice)), item_disc=abs(d),
                tax_rate=products[l.productId].tax_rate, qty=l.qty, avg_cost=products[l.productId].avg_cost,
                locked=products[l.productId].lock_disc, own_price=_own_price(l, products[l.productId], wholesale_pct),
            )
            for l, d in zip(payload.lines, raw_item_discs)
        ],
        payload.discPercent, payload.flatDisc,
    )
    if (payload.discPercent > 0 or payload.flatDisc > 0) and bill.discountable <= 0:
        sold = [products[l.productId] for i, l in enumerate(payload.lines) if not l.isReturn and not bill.below_cost[i]]
        if sold and all(p.lock_disc for p in sold):
            raise SaleError("Every Item this bill sells has its discount locked, so no bill discount can be given.")
        if any(bill.below_cost):
            raise SaleError(
                "What this bill sells is already priced at or below what it cost the shop, so it can't take a discount. Take the discount off.",
                status=LOSS,
            )
        raise SaleError("Nothing on this bill is being sold, so there's nothing for a bill discount to come off. Take the discount off.")
    item_discs = [(-d if l.isReturn else d) if d else ZERO for l, d in zip(payload.lines, bill.item_discs)]
    bill_disc = bill.bill_disc
    line_discs = [(-d if l.isReturn else d) if d else ZERO for l, d in zip(payload.lines, bill.line_discs)]
    disc_total = sum(item_discs, ZERO) + bill_disc

    # No discount sells at a loss, whoever approved what.
    if bill.refused:
        name, fetches, floor = bill.refused[0]
        raise SaleError(
            f"{name} is on this bill at {sale_rules.rs(fetches)} with tax, less than it cost the shop ({sale_rules.rs(floor)}), "
            "and that isn't its own price. Take it off and ring it up again at its price.",
            status=LOSS,
        )
    if bill_disc > bill.max_bill_disc + sale_rules.TOLERANCE:
        most = bill.max_bill_disc.quantize(Decimal("1"), rounding=ROUND_FLOOR)
        raise SaleError(
            f"A discount of {sale_rules.rs(bill_disc)} would sell this bill below what its Items cost the shop. "
            f"The most discount it can take is {sale_rules.rs(most)}. Lower the discount.",
            status=LOSS,
        )

    gst = sum(
        ((_line_gross(l, l.unitPrice) - line_disc) * products[l.productId].tax_rate / Decimal("100"))
        for l, line_disc in zip(payload.lines, line_discs)
    )
    grand_total = gross - disc_total + payload.fare + gst
    if grand_total < 0:
        raise SaleError(
            "This bill comes to less than nothing, so it can't be paid. Give the refund from Returns, or add what the customer is taking.",
            status=LOSS,
        )
    net_value = grand_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    # Only the salesperson's own bill discount counts against their authority. An Item's discount was
    # decided when the Item was set up, and asking a manager to re-approve it on every sale would
    # train everyone to click through the approval. It is measured on the margin the floors leave, not on the price:
    # a 10% limit gives away a tenth of the profit left in the bill.
    effective_pct = bill.authority_percent()
    override_user = None
    from app.services.rbac_service import discount_limit_of

    own_limit = discount_limit_of(cashier)
    if effective_pct > own_limit + discount_approval_service.PERCENT_TOLERANCE:
        if not payload.discountApprovalToken:
            raise SaleError(
                f"This discount is {effective_pct:.1f}% of the profit on the bill, more than your {own_limit.normalize():f}% limit, "
                "so someone with a higher limit needs to approve it"
            )
        # A signed approval from POST /sales/discount-approvals, never a bare user id: see
        # discount_approval_service for what the old id-on-trust check let a salesperson do.
        try:
            override_user = await discount_approval_service.verify(
                payload.discountApprovalToken, cashier, payload.clientRequestId, effective_pct
            )
        except discount_approval_service.ApprovalError as exc:
            raise SaleError(exc.message) from exc

    received = sum(payload.tenders.values(), ZERO)
    if received < net_value:
        raise SaleError(f"Payment not covered. Still to pay: {money_str(net_value - received)}")
    cash_back = max(ZERO, received - net_value)
    # Change only ever comes out of the cash handed over: a card, wallet, bank, voucher, points or credit payment is for
    # what is due, never more (dry run B12: a card-only bill gave Rs 0.40 back).
    if cash_back > payload.tenders.get("CASH", ZERO):
        raise SaleError("Only cash gives change back. Lower the card, online or other payment to what is due.")

    # Members and points. Earned on what the customer actually paid: never on what points paid for.
    details = await _tender_details(payload)
    # A slip's payment is an amount collected, nothing more: card and online payments on it don't sign anyone up.
    member = None if slip is not None else await _resolve_member(payload, party, cashier)
    loyalty = await members_service.settings()
    points_amount = payload.tenders.get("POINTS", ZERO)
    points_redeemed = 0
    if points_amount > 0:
        if not member:
            raise SaleError("Paying with points needs the member's code or mobile number.")
        if not loyalty.enabled:
            raise SaleError("Loyalty points are switched off.")
        try:
            points_redeemed = members_service.redeem_points(points_amount, loyalty)
        except members_service.MemberError as exc:
            raise SaleError(exc.message) from exc
        if points_redeemed > member.points_balance:
            raise SaleError(f"{member.name} has {member.points_balance} points, not {points_redeemed}.")
        if points_redeemed < loyalty.min_redeem_points:
            raise SaleError(f"Points can be used from {loyalty.min_redeem_points} at a time.")
        cap = (net_value * loyalty.max_redeem_percent / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if points_amount > cap:
            raise SaleError(f"Points can pay up to {loyalty.max_redeem_percent.normalize():f}% of a bill, which is Rs {cap} on this one.")
    earned_points = 0
    if member and loyalty.enabled:
        earned_points = members_service.points_for_amount(net_value - points_amount, loyalty)

    invoice_number = await next_invoice_number()
    credit_amount = payload.tenders.get("CREDIT", ZERO)
    is_credit_sale = credit_amount > 0

    # Credit-limit enforcement (previously: creditBalance was seeded but never checked or
    # updated anywhere: legacy's "Check Balance Limit" had no equivalent at all).
    if is_credit_sale:
        if not party.credit_allowed:
            raise SaleError(f"{party.name} is not allowed credit sales")
        new_balance = party.credit_balance + credit_amount
        if new_balance > party.credit_limit:
            headroom = party.credit_limit - party.credit_balance
            raise SaleError(
                f"Credit sale of {money_str(credit_amount)} exceeds {party.name}'s "
                f"remaining headroom of {money_str(headroom)}"
            )
        party.credit_balance = new_balance
        await party.save(update_fields=["credit_balance"])

    sale = await SaleRecord.create(
        invoice_number=invoice_number,
        cashier=cashier,
        party=party,
        till_session=till,
        gross=gross,
        disc_total=disc_total,
        fare=payload.fare,
        gst=gst,
        grand_total=grand_total,
        net_value=net_value,
        discount_override_by=override_user,
        earned_points=earned_points,
        member=member,
        points_redeemed=points_redeemed,
        received=received,
        cash_back=cash_back,
        is_credit_sale=is_credit_sale,
        # Filled below once the bill is written (services/fbr_service.py): a test number in dummy mode, FBR's own number
        # once FBR has taken it. Empty until then, and on a branch with FBR invoices off.
        fbr_invoice_number="",
        client_request_id=payload.clientRequestId,
        # The pharmacy slip this sale is the payment of, and the Pharmacist who made it.
        slips=[slip] if slip is not None else None,
    )

    location = await Location.get(id=DEFAULT_LOCATION_ID)
    for index, (line, line_disc) in enumerate(zip(payload.lines, line_discs)):
        product = products[line.productId]
        await SaleLine.create(
            sale=sale, product=product, qty=line.qty, unit_price=line.unitPrice, is_return=line.isReturn,
            alias_code=aliases[index].code if index in aliases else None,
            sell_level=line.level, level_qty=line.levelQty if line.level else None, level_price=line.levelPrice if line.level else None,
            return_of_invoice=returns_of.get(id(line)),
            level_detail=sell_levels.detail(product, line.level) if line.level else None,
            disc_amount=line_disc.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            unit_cost=product.avg_cost,
            tax_amount=((_line_gross(line, line.unitPrice) - line_disc) * product.tax_rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )

    # Returns on the bill go back on the shelf first; each sale then takes from Main Store, then the branch's other
    # locations, never leaving any of them below zero.
    now = datetime.now(timezone.utc)
    for line in sorted(payload.lines, key=lambda l: not l.isReturn):
        product = products[line.productId]
        per_unit = sell_levels.pieces_per_unit(product)
        if line.isReturn:
            # Loose pieces go back as pieces, keeping Main Store's figure a whole number of them.
            qty = await stock_guard.return_pieces(product, loose_pieces[id(line)], per_unit) if id(line) in loose_pieces else line.qty
            await StockMovement.create(product=product, location=location, kind="return", qty=qty, origin_user=cashier, at=now, unit_cost=product.avg_cost)
            continue
        plan = (
            await stock_guard.plan_pieces(product, loose_pieces[id(line)], per_unit, past_zero=past_zero) if id(line) in loose_pieces
            else await stock_guard.plan_sale(product, line.qty, past_zero=past_zero)
        )
        for place, qty, reason in plan:
            await StockMovement.create(
                product=product, location=place, kind="sell", qty=-qty, reason=reason, origin_user=cashier, at=now, unit_cost=product.avg_cost,
            )

    for code, amount in payload.tenders.items():
        if amount > 0:
            await SaleTender.create(sale=sale, code=code, amount=amount, **details.get(code, {}))

    if member:
        if points_redeemed:
            await members_service.add_entry(member, "redeem", -points_redeemed, invoice_number=invoice_number, note=None, user=cashier)
        if earned_points:
            await members_service.add_entry(member, "earn", earned_points, invoice_number=invoice_number, note=None, user=cashier)
        await member.refresh_from_db()

    voucher_amount = payload.tenders.get("VOUCHER", ZERO)
    if voucher_amount > 0:
        if not payload.voucherCode:
            raise SaleError("Voucher code required for a VOUCHER tender")
        try:
            # The sale's own customer, so a voucher that belongs to someone only goes on their bill.
            await gift_voucher_service.redeem(payload.voucherCode, voucher_amount, invoice_number, str(party.id))
        except gift_voucher_service.VoucherError as exc:
            raise SaleError(exc.message) from exc

    # Scan history: the lines still open on this bill were sold.
    from app.services import scan_history_service

    await scan_history_service.mark_sold(payload.clientRequestId, invoice_number)

    # The bill's FBR invoice, in this same transaction: a test number in dummy mode, or the invoice ready to go to FBR
    # once the bill has committed. It is never sent from inside the sale, so FBR can't hold up the till.
    from app.services import fbr_service

    await fbr_service.issue_for_sale(sale)

    await OutboxEvent.create(
        aggregate_type="SaleRecord",
        aggregate_id=str(sale.id),
        payload={
            "invoiceNumber": invoice_number, "netValue": str(net_value), "partyId": str(party.id),
            "memberCode": member.code if member else None,
        },
        origin_user_id=str(cashier.id), origin_device_id=get_device_id(),
    )

    await sale.fetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by", "member")
    return sale


async def find_by_invoice(invoice_number: str) -> SaleRecord | None:
    sale = await SaleRecord.get_or_none(invoice_number=invoice_number.strip().upper())
    if sale:
        await sale.fetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by", "member")
    return sale


async def sees_every_bill(user: User) -> bool:
    """Who sees every bill of the branch: those who see every till (a Branch Manager, the Counter board, Staff on duty:
    services/till_service.py sees_all_tills), and those given Reports or the Dashboard, whose figures are the whole
    branch's. Anyone else sees only the bills they rang themselves: no salesperson sees another's till, or its bills. A
    single bill looked up by its number (for a return, or a reprint) is not a list, and stays open to them."""
    from app.services import till_service
    from app.services.rbac_service import has_permission

    if await till_service.sees_all_tills(user):
        return True
    return await has_permission(user, "reports", "R") or await has_permission(user, "branch-console.dashboard", "R")


async def list_sales(
    from_at: datetime | None, to_at: datetime | None, limit: int, offset: int, cashier_id=None,
) -> tuple[list[SaleRecord], int]:
    """Branch-wide, not terminal-scoped: the read path X/Z, the Dashboard and Reports need
    instead of each browser's own local sales journal. `cashier_id` keeps it to one person's bills (sees_every_bill)."""
    qs = SaleRecord.all() if cashier_id is None else SaleRecord.filter(cashier_id=cashier_id)
    if from_at:
        qs = qs.filter(at__gte=from_at)
    if to_at:
        qs = qs.filter(at__lte=to_at)
    total = await qs.count()
    sales = (
        await qs.order_by("-at")
        .offset(offset)
        .limit(limit)
        .prefetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by", "member")
    )
    return sales, total
