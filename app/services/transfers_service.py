"""Transfers in and out of this branch. See models/transfer.py for the two directions.

Every shipment goes step by step, and nothing moves until the step before it is done:

  Coming in (from the godown or another branch)
    1. The sender asks — this branch says it can be sent (acknowledge) or declines with a reason.
    2. The sender dispatches it.
    3. This branch counts what arrived into a location, then accepts it or opens a dispute.

  Going out (to another branch)
    1. Someone asks to send; without approval rights it waits for someone who has them.
    2. The other branch says it can be sent (relayed by head office) — or declines.
    3. This branch dispatches it: the stock leaves its location only now.
    4. The other branch receives it and accepts or disputes; that comes back by sync.
    Head office can also ask this branch to send (another branch's approved stock request, or head office's own
    suggestion): the shipment arrives here with no location, and whoever dispatches it says where it leaves from.

A branch that stays offline for a long time can be sent to without its acknowledgement: head office's
Warehouse Manager does that with a written reason, and this branch is told.

Every line carries its Item described in full (see "the Item on a transfer line" below), so an Item arrives here
with its department, pack, pieces and barcodes, and nobody has to fill them in before it is sold.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from tortoise.transactions import atomic

from app.core.device_context import get_device_id
from app.core.pk_time import pk_day
from app.models import KnownBranch, Location, OutboxEvent, Product, ProductAlias, StockMovement, Transfer, TransferLine, User, balance_for

ZERO = Decimal("0")
OPEN_INBOUND = ("approved", "dispatched", "in_transit")
# The destination's answer that lets a shipment leave: agreed, not needed (it asked for it), or waived by head office.
READY_TO_SEND = ("acknowledged", "skipped", "overridden")
# How far along a shipment is. A message about an earlier step never undoes a later one.
RANK = {"awaiting_approval": 0, "requested": 1, "approved": 2, "dispatched": 3, "in_transit": 3, "held": 3, "received": 4, "received_short": 4}

RECEIVERS = [("inventory.transfers", "W")]
SENDERS = [("inventory.transfers.send", "W"), ("inventory.transfers.approve", "X")]


class TransferError(Exception):
    def __init__(self, message: str):
        self.message = message


async def list_all(direction: str | None = None) -> list[Transfer]:
    qs = Transfer.all()
    if direction in ("inbound", "outbound"):
        qs = qs.filter(direction=direction)
    return await qs.prefetch_related(
        "lines", "location", "dispatched_by", "received_by", "held_by", "requested_by", "approved_by", "ack_by",
    ).order_by("-requested_at")


async def known_branches() -> list[KnownBranch]:
    return await KnownBranch.all().order_by("name")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _label(transfer: Transfer) -> str:
    return transfer.number or "The shipment"


async def _event(user: User | None, transfer: Transfer, payload: dict) -> None:
    await OutboxEvent.create(
        aggregate_type="Transfer", aggregate_id=str(transfer.id), payload=payload,
        origin_user_id=str(user.id) if user else None, origin_device_id=get_device_id(),
    )


async def _notify(kind: str, transfer: Transfer, title: str, body: str | None, audience, users=None, tone: str = "info") -> None:
    from app.services import alerts_service

    await alerts_service.notify(
        kind, title, body=body, link="/inventory/transfers", tone=tone, audience_any=audience, users=users,
        subject=("transfer", str(transfer.id)),
    )


# ── coming in ──────────────────────────────────────────────────────────────────────────────────

async def _inbound_waiting(transfer_id: str) -> Transfer:
    transfer = await Transfer.get_or_none(id=transfer_id, direction="inbound")
    if not transfer:
        raise TransferError("That incoming shipment doesn't exist.")
    if transfer.ack_status != "awaiting" or transfer.status not in ("approved", "requested"):
        raise TransferError(f"{_label(transfer)} isn't waiting for this branch's answer.")
    return transfer


@atomic()
async def acknowledge_inbound(user: User, transfer_id: str, note: str | None) -> Transfer:
    """This branch says the shipment can be sent."""
    transfer = await _inbound_waiting(transfer_id)
    now = _now()
    transfer.ack_status, transfer.ack_at, transfer.ack_by, transfer.ack_note = "acknowledged", now, user, (note or "").strip()[:255] or None
    transfer.ack_by_name = user.name
    await transfer.save()
    await _event(user, transfer, {"event": "acknowledged", "transferId": str(transfer.id), "note": transfer.ack_note, "by": user.name, "at": now.isoformat()})
    await transfer.fetch_related("lines")
    return transfer


@atomic()
async def decline_inbound(user: User, transfer_id: str, reason: str | None) -> Transfer:
    """This branch says don't send it — no space, not needed, wrong Items — and why."""
    if not (reason or "").strip():
        raise TransferError("Say why it shouldn't be sent. The sender needs to know what to change.")
    transfer = await _inbound_waiting(transfer_id)
    now = _now()
    transfer.ack_status, transfer.ack_at, transfer.ack_by, transfer.ack_note = "declined", now, user, reason.strip()[:255]
    transfer.ack_by_name = user.name
    await transfer.save()
    await _event(user, transfer, {"event": "declined", "transferId": str(transfer.id), "note": transfer.ack_note, "by": user.name, "at": now.isoformat()})
    await transfer.fetch_related("lines")
    return transfer


@atomic()
async def open_dispute(user: User, transfer_id: str, note: str) -> Transfer:
    transfer = await Transfer.get_or_none(id=transfer_id)
    if not transfer:
        raise TransferError("Transfer not found")
    words = " ".join((note or "").split())
    if not words:
        raise TransferError("Say what's wrong with it.")
    now = datetime.now(timezone.utc)
    previous = (transfer.dispute_note or "").strip()
    transfer.dispute_open = True
    # Opened again after an earlier dispute: the earlier report and how it was settled stay on record.
    transfer.dispute_note = f"{previous} · Reopened by {user.name} on {pk_day(now):%d %b %Y}: {words}" if previous else words
    await transfer.save()
    if transfer.origin != "local":
        await _event(user, transfer, {"event": "dispute_opened", "transferId": str(transfer.id), "note": words, "by": user.name, "at": now.isoformat()})
    await transfer.fetch_related("lines")
    return transfer


@atomic()
async def receive(
    user: User, transfer_id: str, location_id: str, received: dict[str, Decimal], note: str | None, dispute: bool = False,
) -> Transfer:
    """Count what arrived into a location, then accept it — or dispute it: less than was sent always opens a
    dispute, and so does anything else wrong (damage, wrong Items) when the receiver says so."""
    transfer = await Transfer.get_or_none(id=transfer_id).prefetch_related("lines__product")
    if not transfer or transfer.direction != "inbound":
        raise TransferError("That inbound transfer doesn't exist.")
    if transfer.status in ("received", "received_short"):
        raise TransferError(f"{transfer.number or 'This transfer'} was already received.")
    if transfer.status == "held":
        raise TransferError(f"{transfer.number or 'This shipment'} is on hold ({transfer.hold_reason}). Release it before receiving it.")
    if transfer.status not in ("dispatched", "in_transit"):
        raise TransferError(f"{transfer.number or 'This transfer'} hasn't been dispatched yet, so there's nothing to receive.")
    if dispute and not (note or "").strip():
        raise TransferError("Say what's wrong so the sender can settle it.")
    location = await Location.get_or_none(id=location_id)
    if not location or not location.active:
        raise TransferError("Pick an active location to receive into.")

    now = _now()
    short = False
    lines_out = []
    for line in transfer.lines:
        qty = received.get(str(line.product_id))
        if qty is None:
            raise TransferError(f"Enter what arrived of {line.product.name}, or 0 if none did.")
        if qty < ZERO or qty > line.qty_sent:
            raise TransferError(f"{line.product.name}: received must be between 0 and the {line.qty_sent.normalize():f} sent.")
        line.qty_received = qty
        await line.save(update_fields=["qty_received"])
        if qty < line.qty_sent:
            short = True
        if qty > ZERO:
            # Arrives at what it cost the sender, and averages in with what's already on the shelf.
            if line.unit_cost is not None:
                from app.services import price_history_service

                product = line.product
                cost_before = price_history_service.snapshot(product)
                on_hand = await balance_for(product.id)
                if on_hand > ZERO and product.avg_cost:
                    product.avg_cost = (product.avg_cost * on_hand + qty * line.unit_cost) / (on_hand + qty)
                else:
                    product.avg_cost = line.unit_cost
                await product.save(update_fields=["avg_cost"])
                await price_history_service.record(product, cost_before, "transfer", transfer.number, user)
            await StockMovement.create(
                product_id=line.product_id, location=location, kind="transfer-in", qty=qty,
                reason=transfer.number, origin_user=user, at=now, unit_cost=line.unit_cost,
            )
        lines_out.append({"sku": line.sku or line.product.sku, "qtyReceived": str(qty)})

    transfer.status = "received_short" if short else "received"
    transfer.received_at = now
    transfer.received_by = user
    transfer.location = location
    if short or dispute:
        transfer.dispute_open = True
        transfer.dispute_note = (note or "").strip() or "Short receipt: less arrived than was sent."
    await transfer.save()
    if transfer.origin != "local":
        await _event(user, transfer, {
            "event": "received", "transferId": str(transfer.id), "lines": lines_out,
            "note": transfer.dispute_note if transfer.dispute_open else (note or None), "dispute": transfer.dispute_open,
            "receivedAt": now.isoformat(), "receivedBy": user.name,
        })
    await transfer.fetch_related("lines")
    return transfer


@atomic()
async def hold_inbound(user: User, transfer_id: str, reason: str) -> Transfer:
    transfer = await Transfer.get_or_none(id=transfer_id, direction="inbound")
    if not transfer:
        raise TransferError("That incoming shipment doesn't exist.")
    if transfer.status not in ("dispatched", "in_transit"):
        raise TransferError(f"{transfer.number or 'This shipment'} can't be put on hold because it's {transfer.status.replace('_', ' ')}.")
    if not (reason or "").strip():
        raise TransferError("Say why it's being held, for example damaged, wrong goods or not expected.")
    now = _now()
    transfer.status = "held"
    transfer.hold_reason = reason.strip()[:255]
    transfer.held_at = now
    transfer.held_by = user
    await transfer.save()
    if transfer.origin != "local":
        await _event(user, transfer, {"event": "held", "transferId": str(transfer.id), "reason": transfer.hold_reason, "heldBy": user.name, "heldAt": now.isoformat()})
    await transfer.fetch_related("lines")
    return transfer


@atomic()
async def release_inbound(user: User, transfer_id: str) -> Transfer:
    transfer = await Transfer.get_or_none(id=transfer_id, direction="inbound")
    if not transfer or transfer.status != "held":
        raise TransferError("That shipment isn't on hold.")
    transfer.status = "dispatched"
    await transfer.save()
    if transfer.origin != "local":
        await _event(user, transfer, {"event": "released", "transferId": str(transfer.id), "releasedBy": user.name, "releasedAt": _now().isoformat()})
    await transfer.fetch_related("lines")
    return transfer


# ── going out ──────────────────────────────────────────────────────────────────────────────────

async def _check_stock(transfer: Transfer) -> None:
    """The stock must still be there — a send that waited may find some of it sold meanwhile."""
    from app.services import inventory_service

    await transfer.fetch_related("lines__product", "location")
    location = transfer.location
    if not location or not location.active:
        raise TransferError("The location this shipment leaves from is switched off. Cancel it and send again.")
    for line in transfer.lines:
        held = await inventory_service.balance_for(str(line.product_id), location.id)
        if held < line.qty_sent:
            raise TransferError(f"{location.name} now holds {held.normalize():f} of {line.product.name}; this shipment needs {line.qty_sent.normalize():f}.")


@atomic()
async def dispatch_outbound(
    user: User, destination_code: str, location_id: str, lines: list[tuple[str, Decimal]],
    vehicle: str | None, driver: str | None, notes: str | None,
) -> Transfer:
    """Ask to send stock to another branch. Nothing leaves yet: it needs approval here (unless the person
    asking may approve), then the other branch's go-ahead, then a dispatch."""
    from app.services import inventory_service, registration_service
    from app.services.rbac_service import has_permission

    identity = await registration_service.current()
    if identity is None:
        raise TransferError("This branch isn't verified with head office yet, so it can't send stock to another branch.")
    destination = await KnownBranch.get_or_none(code=destination_code.strip().upper())
    if not destination:
        raise TransferError("Pick a branch from the list head office sent. If it's missing, sync first.")
    if destination.code == identity.code:
        raise TransferError("A branch can't send stock to itself.")
    location = await Location.get_or_none(id=location_id)
    if not location or not location.active:
        raise TransferError("Pick an active location the stock leaves from.")
    if not lines:
        raise TransferError("Add at least one Item to send.")

    products: dict[str, Product] = {}
    for product_id, qty in lines:
        if product_id in products:
            raise TransferError("An Item is on the transfer twice. Put the whole quantity on one line.")
        product = await Product.get_or_none(id=product_id)
        if not product:
            raise TransferError(f"Unknown Item {product_id}")
        if qty <= ZERO:
            raise TransferError(f"{product.name}: enter a quantity above zero.")
        held = await inventory_service.balance_for(product_id, location.id)
        if held < qty:
            raise TransferError(f"{location.name} only holds {held.normalize():f} of {product.name}; can't send {qty.normalize():f}.")
        products[product_id] = product

    # A second click, a browser retry or another tab: the same person asked to send the same thing a moment ago and it
    # hasn't left yet, so hand back that shipment instead of asking to send the stock twice.
    from datetime import timedelta

    since = _now() - timedelta(seconds=60)
    wanted = sorted((str(product_id), Decimal(qty)) for product_id, qty in lines)
    recent = await Transfer.filter(
        direction="outbound", origin="branch", requested_by_id=user.id, counterparty_code=destination.code, location_id=location.id,
        status__in=["awaiting_approval", "requested", "approved"],
    ).order_by("-requested_at").limit(5).prefetch_related("lines")
    for earlier in recent:
        if _aware(earlier.requested_at) < since:
            break
        if sorted((str(l.product_id), l.qty_sent) for l in earlier.lines) == wanted:
            return earlier

    from app.services import numbering_service

    now = _now()
    number = await numbering_service.next_number("transfer_out", Transfer, "number", f"{identity.code}-TR-", 4, direction="outbound")
    can_approve = await has_permission(user, "inventory.transfers.approve", "X")
    transfer = await Transfer.create(
        number=number, direction="outbound", origin="branch",
        from_warehouse=destination.name, counterparty_code=destination.code, status="awaiting_approval",
        vehicle=(vehicle or "").strip() or None, driver=(driver or "").strip() or None, notes=(notes or "").strip() or None,
        location=location, requested_by=user, requested_at=now,
    )
    for product_id, qty in lines:
        product = products[product_id]
        await TransferLine.create(transfer=transfer, product=product, sku=product.sku, qty_sent=qty)
    if can_approve:
        await _ask_destination(user, transfer, approved_by=None)
    await transfer.fetch_related("lines")
    return transfer


async def _ask_destination(user: User, transfer: Transfer, approved_by: User | None) -> None:
    """Approved here: ask the other branch (through head office) whether it can be sent."""
    await _check_stock(transfer)
    now = _now()
    transfer.status = "requested"
    transfer.ack_status = "awaiting"
    transfer.ack_requested_at = now
    transfer.approved_by = approved_by
    await transfer.save()
    await _event(user, transfer, {"event": "requested", "transfer": {
        "id": str(transfer.id), "number": transfer.number, "destinationCode": transfer.counterparty_code,
        "vehicle": transfer.vehicle, "driver": transfer.driver, "notes": transfer.notes, "requestedAt": now.isoformat(),
        "requestedBy": (await User.get_or_none(id=transfer.requested_by_id)).name if transfer.requested_by_id else user.name,
        "approvedBy": approved_by.name if approved_by else None,
        "lines": await _lines_out(transfer),
    }})


@atomic()
async def approve_outbound(user: User, transfer_id: str) -> Transfer:
    transfer = await Transfer.get_or_none(id=transfer_id, direction="outbound")
    if not transfer:
        raise TransferError("That outgoing shipment doesn't exist.")
    if transfer.status != "awaiting_approval":
        raise TransferError(f"{transfer.number} isn't waiting for approval. It's {transfer.status.replace('_', ' ')}.")
    await _ask_destination(user, transfer, approved_by=user)
    await transfer.fetch_related("lines")
    return transfer


@atomic()
async def dispatch_ready(
    user: User, transfer_id: str, vehicle: str | None, driver: str | None, location_id: str | None = None,
) -> Transfer:
    """The other branch agreed: the stock leaves its location now, and head office relays it.

    A shipment head office asked this branch to send (a branch's stock request, or the Executive's suggestion) has
    no location yet, so the person dispatching says where it leaves from."""
    transfer = await Transfer.get_or_none(id=transfer_id, direction="outbound")
    if not transfer:
        raise TransferError("That outgoing shipment doesn't exist.")
    if transfer.status != "approved" or transfer.ack_status not in READY_TO_SEND:
        if transfer.ack_status == "awaiting":
            raise TransferError(f"{transfer.from_warehouse} hasn't agreed to receive {transfer.number} yet.")
        if transfer.ack_status == "declined":
            raise TransferError(f"{transfer.from_warehouse} declined {transfer.number}: {transfer.ack_note or 'no reason given'}.")
        raise TransferError(f"{transfer.number} can't be dispatched because it's {transfer.status.replace('_', ' ')}.")
    if location_id:
        location = await Location.get_or_none(id=location_id)
        if not location or not location.active:
            raise TransferError("Pick an active location the stock leaves from.")
        transfer.location = location
    elif not transfer.location_id:
        raise TransferError("Pick the location the stock leaves from.")
    transfer.vehicle = (vehicle or "").strip() or transfer.vehicle
    transfer.driver = (driver or "").strip() or transfer.driver
    if not transfer.vehicle or not transfer.driver:
        raise TransferError("A dispatch needs the vehicle and the driver.")
    await _check_stock(transfer)
    now = _now()
    for line in transfer.lines:
        line.unit_cost = line.product.avg_cost
        await line.save(update_fields=["unit_cost"])
        await StockMovement.create(
            product=line.product, location=transfer.location, kind="transfer-out", qty=-line.qty_sent,
            reason=transfer.number, origin_user=user, at=now, unit_cost=line.unit_cost,
        )
    transfer.status = "dispatched"
    transfer.dispatched_by = user
    transfer.dispatched_at = now
    await transfer.save()
    await _event(user, transfer, {"event": "dispatched", "transfer": {
        "id": str(transfer.id), "number": transfer.number, "destinationCode": transfer.counterparty_code,
        "vehicle": transfer.vehicle, "driver": transfer.driver, "notes": transfer.notes, "dispatchedAt": now.isoformat(),
        "dispatchedBy": user.name,
        "lines": await _lines_out(transfer),
    }})
    await transfer.fetch_related("lines")
    return transfer


@atomic()
async def cancel_outbound(user: User, transfer_id: str, reason: str | None) -> Transfer:
    from app.services.rbac_service import has_permission

    transfer = await Transfer.get_or_none(id=transfer_id, direction="outbound")
    if not transfer:
        raise TransferError("That outgoing shipment doesn't exist.")
    if transfer.status not in ("awaiting_approval", "requested", "approved"):
        raise TransferError(f"{transfer.number} has already left, so it can't be cancelled.")
    if transfer.requested_by_id != user.id and not await has_permission(user, "inventory.transfers.approve", "X"):
        raise TransferError("Only the person who asked, or someone who approves shipments, can cancel it.")
    told_head_office = transfer.status != "awaiting_approval"
    transfer.status = "cancelled"
    earlier = (transfer.notes or "").rstrip(". ")
    cancelled = f"Cancelled: {reason.strip()}" if reason and reason.strip() else "Cancelled"
    transfer.notes = (f"{earlier}. {cancelled}" if earlier else cancelled)[:255]
    await transfer.save()
    if told_head_office:
        await _event(user, transfer, {"event": "cancelled", "transferId": str(transfer.id), "reason": (reason or "").strip() or None, "by": user.name})
    await transfer.fetch_related("lines")
    return transfer


# ── the Item on a transfer line ─────────────────────────────────────────────────────────────────
#
# Every line carries the whole Item as its sender keeps it: head office sends its godown Item, and a branch sending to
# another branch sends its own, which head office passes on as it came. An Item new here is made with all of it,
# prices included. An Item this branch already has only gets what it has left blank: its prices, and anything
# already set here, stay as they are. The pack, the pieces and the reorder level count in the Item's unit, so they are
# filled in only on an Item stocked in the same unit as the sender's.
#
# A shipment head office asks another branch to send reaches this branch before the sending branch has described its
# Items, so its lines say `provisional` and carry head office's copy. An Item new here from such a line is marked
# "details to complete"; when the sending branch's own description arrives (with the dispatch), the Item takes all of
# it, as long as nobody has saved it or stocked it since.

# Line key, this branch's column, the longest value the column holds.
_TEXT = (
    ("department", "department", 80), ("category", "category", 80), ("itemClass", "item_class", 80), ("subclass", "subclass", 80),
    ("brand", "brand", 120), ("manufacturer", "manufacturer", 120), ("variant", "variant", 60),
)
# The prices besides the sale price, set only on an Item new here.
_PRICES = (
    ("rpp", "rpp"), ("wholesalePrice", "wholesale_price"), ("piecePrice", "piece_price"), ("stripPrice", "strip_price"),
    ("packPrice", "pack_price"), ("boxPrice", "box_price"),
)
# Counted in the Item's unit: filled in only where the units match.
_IN_UNITS = ("pack_unit", "pack_size", "packs_per_box", "pieces_per_unit", "piece_unit", "pieces_per_strip", "reorder_level")
_MAX_PIECES = 1000


def _plain(value) -> str | None:
    """A number as it travels: plain digits, never 1E+1."""
    return None if value is None else format(Decimal(str(value)).normalize(), "f")


async def describe(product: Product) -> dict:
    """This branch's Item as a transfer line carries it to another branch."""
    aliases = await ProductAlias.filter(product_id=product.id)
    return {
        "sku": product.sku, "name": product.name, "unit": product.unit, "isWeighed": product.is_weighed,
        "taxRate": _plain(product.tax_rate), "price": _plain(product.price),
        **{key: _plain(getattr(product, column)) for key, column in _PRICES},
        "barcode": product.barcode,
        "aliases": [
            {"code": a.code, "qty": _plain(a.qty), "remarks": a.remarks, "discPercent": _plain(a.disc_percent), "discFlat": _plain(a.disc_flat)}
            for a in aliases
        ],
        **{key: getattr(product, column) for key, column, _ in _TEXT},
        "origin": product.origin, "packUnit": product.pack_unit, "packSize": product.pack_size, "packsPerBox": product.packs_per_box,
        "piecesPerUnit": product.pieces_per_unit, "pieceUnit": product.piece_unit, "piecesPerStrip": product.pieces_per_strip,
        "lockDisc": product.lock_disc, "reorderLevel": _plain(product.reorder_level),
        "needsDetails": product.needs_details, "detailsNote": product.details_note if product.needs_details else None,
    }


async def _lines_out(transfer: Transfer) -> list[dict]:
    """The lines of a shipment this branch sends, each Item described in full for the branch receiving it."""
    return [
        {
            **await describe(line.product), "qtySent": str(line.qty_sent),
            "unitCost": str(line.unit_cost if line.unit_cost is not None else line.product.avg_cost or 0),
        }
        for line in transfer.lines
    ]


def _text(line: dict, key: str, size: int) -> str | None:
    return str(line.get(key) or "").strip()[:size] or None


def _number(line: dict, key: str) -> Decimal | None:
    value = line.get(key)
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _count(line: dict, key: str, top: int | None = None) -> int | None:
    number = _number(line, key)
    if number is None or number < 1 or number != number.to_integral_value() or (top and number > top):
        return None
    return int(number)


def _money(line: dict, key: str) -> Decimal | None:
    number = _number(line, key)
    return None if number is None else number.quantize(Decimal("0.01"))


def _described(line: dict) -> dict:
    """What a line says about its Item, as this branch's columns. Left out: what the line leaves blank."""
    origin = str(line.get("origin") or "").strip().lower()
    out = {
        **{column: _text(line, key, size) for key, column, size in _TEXT},
        "origin": origin if origin in ("local", "imported") else None,
        "pack_unit": _text(line, "packUnit", 40), "pack_size": _count(line, "packSize"), "packs_per_box": _count(line, "packsPerBox"),
        "pieces_per_unit": _count(line, "piecesPerUnit", _MAX_PIECES), "piece_unit": _text(line, "pieceUnit", 20),
        "pieces_per_strip": _count(line, "piecesPerStrip", _MAX_PIECES), "reorder_level": _number(line, "reorderLevel"),
    }
    return {column: value for column, value in out.items() if value is not None}


async def _free_code(code, sku: str, product_id: str | None = None) -> str | None:
    """A barcode this branch can give the Item: not blank, not its own code, and not ringing up anything else here."""
    from app.services import catalog_service

    code = str(code or "").strip()
    if not code or len(code) > 40 or code == sku:
        return None
    return None if await catalog_service.code_owner(code, product_id) else code


async def _add_aliases(product: Product, line: dict) -> None:
    """The sender's alternate barcodes (a carton's, a strip's), leaving out any that already ring up something here."""
    from app.services import catalog_service

    seen = {product.sku, product.barcode}
    for alias in line.get("aliases") or []:
        code = str((alias or {}).get("code") or "").strip()
        qty = _number(alias, "qty")
        if not code or len(code) > 60 or code in seen or not qty or await catalog_service.code_owner(code):
            continue
        seen.add(code)
        await ProductAlias.create(
            product=product, code=code, remarks=_text(alias, "remarks", 255), qty=qty,
            disc_percent=_number(alias, "discPercent") or Decimal("0"), disc_flat=_money(alias, "discFlat") or Decimal("0"),
        )


def _provisional_note(number: str | None) -> str:
    return f"Came on {number or 'a shipment'} as head office has it. The sending branch's own details replace these when it dispatches."[:200]


def _carried_note(line: dict) -> str | None:
    """An Item the sender itself still has to complete arrives marked the same way here."""
    if not line.get("needsDetails"):
        return None
    return f"Still to complete where it came from: {str(line.get('detailsNote') or 'check its details').strip()}"[:200]


async def _describe_new(product: Product | None, sku: str, line: dict, number: str | None) -> Product:
    """Makes the Item from the line, or, given `product`, makes it over from the line (see _still_provisional)."""
    from app.services import price_history_service

    note = _provisional_note(number) if line.get("provisional") else _carried_note(line)
    fields = {
        "name": (str(line.get("name") or "").strip() or sku)[:160], "unit": (_text(line, "unit", 20) or "pc"),
        "is_weighed": bool(line.get("isWeighed")), "tax_rate": _number(line, "taxRate") or Decimal("0"),
        "price": _money(line, "price") or Decimal("0"), "lock_disc": bool(line.get("lockDisc")),
        **{column: _money(line, key) for key, column in _PRICES},
        **{column: None for _, column, _ in _TEXT}, **{column: None for column in _IN_UNITS}, "origin": None,
        **_described(line), "needs_details": note is not None, "details_note": note,
    }
    if product is None:
        product_id = sku if not await Product.exists(id=sku) else f"ho-{sku}"
        product = await Product.create(
            id=product_id[:40], sku=sku[:40], avg_cost=_number(line, "unitCost") or Decimal("0"), remarks="Added by a head office transfer",
            barcode=await _free_code(line.get("barcode"), sku), **fields,
        )
        await _add_aliases(product, line)
        await price_history_service.save_rows([price_history_service.first_price(product, "transfer-new", number)])
        return product
    before = price_history_service.snapshot(product)
    for column, value in fields.items():
        setattr(product, column, value)
    product.barcode = await _free_code(line.get("barcode"), product.sku, product.id)
    await product.save()
    await ProductAlias.filter(product_id=product.id).delete()
    await _add_aliases(product, line)
    await price_history_service.record(product, before, "transfer-new", number)
    return product


async def _still_provisional(product: Product, number: str | None) -> bool:
    """Made here from head office's copy of this very shipment, and not saved, sold or stocked since."""
    return (
        product.needs_details and product.details_note == _provisional_note(number)
        and not await StockMovement.exists(product_id=product.id)
    )


async def _fill_blanks(product: Product, line: dict) -> None:
    """An Item this branch already has takes from the line only what it leaves blank. Nothing set here changes."""
    changed: list[str] = []

    def take(column: str, value) -> None:
        if value is not None and getattr(product, column) in (None, ""):
            setattr(product, column, value)
            changed.append(column)

    described = _described(line)
    for _, column, _ in _TEXT:
        take(column, described.get(column))
    take("origin", described.get("origin"))
    if (product.unit or "").strip().lower() == str(line.get("unit") or "").strip().lower():
        # A pack and the pieces go on whole: a pack size only beside the same pack unit, a box only over the same pack,
        # a piece name and strip only over the same pieces.
        pack_unit, pack_size = described.get("pack_unit"), described.get("pack_size")
        if not product.pack_unit and not product.pack_size:
            take("pack_unit", pack_unit)
            take("pack_size", pack_size)
        elif (product.pack_unit or "").strip().lower() == (pack_unit or "").strip().lower():
            take("pack_size", pack_size)
        if (product.pack_unit or "").strip().lower() == (pack_unit or "").strip().lower() and product.pack_size == pack_size:
            take("packs_per_box", described.get("packs_per_box"))
        take("pieces_per_unit", described.get("pieces_per_unit"))
        if product.pieces_per_unit == described.get("pieces_per_unit"):
            take("piece_unit", described.get("piece_unit"))
            take("pieces_per_strip", described.get("pieces_per_strip"))
        take("reorder_level", described.get("reorder_level"))
    if not product.barcode and line.get("barcode"):
        take("barcode", await _free_code(line.get("barcode"), product.sku, product.id))
    if changed:
        await product.save(update_fields=changed)
    if line.get("aliases") and not await ProductAlias.exists(product_id=product.id):
        await _add_aliases(product, line)


# ── applying what head office sends ─────────────────────────────────────────────────────────────

async def _product_for(line: dict, number: str | None = None) -> Product:
    """This branch's Item for a line's code (by code, barcode or alternate barcode), with whatever it left blank filled
    in from the line; or a new Item made from the line when this branch has never stocked it. `number` is the shipment's."""
    sku = str(line.get("sku") or "").strip()
    if not sku:
        raise TransferError("A transfer line from head office has no Item code.")
    product = await Product.get_or_none(sku=sku) or await Product.get_or_none(barcode=sku)
    if not product:
        alias = await ProductAlias.get_or_none(code=sku).prefetch_related("product")
        product = alias.product if alias else None
    if not product:
        return await _describe_new(None, sku, line, number)
    if not line.get("provisional") and await _still_provisional(product, number):
        return await _describe_new(product, product.sku, line, number)
    await _fill_blanks(product, line)
    return product


def _dt(value):
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return value


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


def _take_answer(transfer: Transfer | None, state: dict) -> dict:
    """The destination's answer as head office has it — unless this branch answered after head office last
    asked, in which case this branch's own answer stands until head office hears of it."""
    incoming = {
        "ack_status": state.get("ackStatus"), "ack_requested_at": _dt(state.get("ackRequestedAt")),
        "ack_at": _dt(state.get("ackAt")), "ack_by_name": state.get("ackBy"), "ack_note": state.get("ackNote"),
        "override_reason": state.get("overrideReason"), "override_by_name": state.get("overrideBy"), "override_at": _dt(state.get("overrideAt")),
    }
    if (
        transfer is not None and transfer.ack_status in ("acknowledged", "declined") and incoming["ack_status"] == "awaiting"
        and transfer.ack_at and (incoming["ack_requested_at"] is None or _aware(transfer.ack_at) >= incoming["ack_requested_at"])
    ):
        return {k: v for k, v in incoming.items() if k.startswith("override")}
    return incoming


def _moves_forward(current: str | None, incoming: str) -> bool:
    if current is None:
        return True
    if incoming == "cancelled":
        return RANK.get(current, 0) < RANK["dispatched"]
    if current == "cancelled":
        return False
    return RANK.get(incoming, 0) >= RANK.get(current, 0)


@atomic()
async def apply_inbound(state: dict) -> str:
    transfer_id = state.get("id")
    if not transfer_id:
        raise TransferError("A transfer from head office has no id.")
    source = state.get("source") or {}
    transfer = await Transfer.get_or_none(id=transfer_id)
    before = (transfer.status, transfer.dispute_open, transfer.override_reason) if transfer else None
    if transfer and transfer.status in ("received", "received_short"):
        # Received here already: only head office's decision on a dispute can still change it — and only
        # from a message sent after head office heard of the receipt, so an older one can't undo it.
        if state.get("status") not in ("received", "received_short"):
            return "older-than-receipt"
        transfer.dispute_open = bool(state.get("disputeOpen"))
        transfer.dispute_note = state.get("disputeNote") or transfer.dispute_note
        await transfer.save(update_fields=["dispute_open", "dispute_note", "updated_at"])
        if before[1] and not transfer.dispute_open:
            await _notify("transfer.dispute_settled", transfer, f"Dispute on {_label(transfer)} settled", transfer.dispute_note, RECEIVERS, tone="good")
        return "dispute-updated"
    incoming_status = state.get("status") or "approved"
    if incoming_status in ("received", "received_short"):
        # Only this branch's own count marks a shipment received here, because that count is what puts the stock on the
        # shelves. Head office saying "received" (say, after a backup was restored here) leaves it to be counted in again.
        from app.core import logs

        kept = transfer.status if transfer else "dispatched"
        logs.log.warning("transfer %s: head office says %s but this branch has it as %s; kept %s", state.get("number") or transfer_id,
                         incoming_status, transfer.status if transfer else "missing", kept)
        incoming_status = kept
    if transfer and transfer.status == "cancelled" and incoming_status in ("dispatched", "in_transit") and state.get("reinstatedByHeadOffice"):
        pass  # head office undid a cancel that came too late: the sender had already sent it, and no stock moved here for the cancel
    elif transfer and not _moves_forward(transfer.status, incoming_status):
        incoming_status = transfer.status
    if transfer and transfer.status == "held" and incoming_status in OPEN_INBOUND:
        incoming_status = "held"  # a hold made here stays until someone here releases it
    fields = dict(
        number=state.get("number"), direction="inbound", origin="cloud",
        from_warehouse=(source.get("name") or "Head office")[:120], counterparty_code=source.get("code"),
        status=incoming_status, vehicle=state.get("vehicle"), driver=state.get("driver"),
        notes=state.get("notes"), requested_at=_dt(state.get("requestedAt")) or _now(),
        dispatched_at=_dt(state.get("dispatchedAt")), dispute_open=bool(state.get("disputeOpen")),
        dispute_note=state.get("disputeNote"), **_take_answer(transfer, state),
    )
    if transfer:
        for key, value in fields.items():
            setattr(transfer, key, value)
        await transfer.save()
        await TransferLine.filter(transfer=transfer).delete()
        outcome = "updated"
    else:
        transfer = await Transfer.create(id=transfer_id, **fields)
        outcome = "created"
    for line in state.get("lines") or []:
        product = await _product_for(line, transfer.number)
        await TransferLine.create(
            transfer=transfer, product=product, sku=line.get("sku"), qty_sent=Decimal(str(line.get("qtySent") or "0")),
            unit_cost=Decimal(str(line["unitCost"])) if line.get("unitCost") not in (None, "") else None,
        )

    # Tell people here what changed. (What they must do next shows as a task by itself.)
    old_status = before[0] if before else None
    if transfer.status == "dispatched" and old_status != "dispatched" and old_status != "held":
        await _notify("transfer.dispatched", transfer, f"{_label(transfer)} is on its way from {transfer.from_warehouse}",
                      f"Vehicle {transfer.vehicle or '-'}, driver {transfer.driver or '-'}. Count it in when it arrives.", RECEIVERS)
    if transfer.override_reason and (not before or not before[2]):
        await _notify("transfer.sent_without_answer", transfer, f"{transfer.from_warehouse} is sending {_label(transfer)} without this branch's go-ahead",
                      f"{transfer.override_by_name or 'Head office'}: “{transfer.override_reason}”", RECEIVERS, tone="warning")
    if transfer.status == "cancelled" and old_status not in (None, "cancelled"):
        await _notify("transfer.cancelled", transfer, f"{_label(transfer)} from {transfer.from_warehouse} was cancelled", transfer.notes, RECEIVERS, tone="warning")
    return outcome


async def _outbound_from_head_office(state: dict) -> Transfer | None:
    """Head office asked this branch to send stock to another branch (an approved stock request, or head office's
    own suggestion). There is no copy here yet: make one, with no location until someone dispatches it."""
    from app.services import registration_service

    identity = await registration_service.current()
    source = state.get("source") or {}
    destination = state.get("destination") or {}
    if identity is None or str(source.get("code") or "").upper() != identity.code.upper() or not state.get("id"):
        return None
    status = state.get("status") or "requested"
    if status in ("cancelled", "received", "received_short"):
        return None  # nothing left for this branch to do; it never saw the shipment
    transfer = await Transfer.create(
        id=state["id"], number=state.get("number"), direction="outbound", origin="cloud",
        from_warehouse=(destination.get("name") or destination.get("code") or "Another branch")[:120],
        counterparty_code=destination.get("code"), status=status, vehicle=state.get("vehicle"), driver=state.get("driver"),
        notes=state.get("notes"), requested_at=_dt(state.get("requestedAt")) or _now(), **_take_answer(None, state),
    )
    for line in state.get("lines") or []:
        product = await _product_for(line, transfer.number)
        await TransferLine.create(
            transfer=transfer, product=product, sku=line.get("sku"), qty_sent=Decimal(str(line.get("qtySent") or "0")),
            unit_cost=Decimal(str(line["unitCost"])) if line.get("unitCost") not in (None, "") else None,
        )
    ready = transfer.status == "approved" and transfer.ack_status in READY_TO_SEND
    await _notify(
        "transfer.asked_to_send", transfer, f"Head office asks this branch to send {_label(transfer)} to {transfer.from_warehouse}",
        (transfer.ack_note or transfer.notes or "") + (" Pick where it leaves from and dispatch it." if ready else f" {transfer.from_warehouse} is asked to agree first."),
        SENDERS, tone="warning" if ready else "info",
    )
    return transfer


@atomic()
async def apply_outbound(state: dict) -> str:
    # A stock request's answer travels on this same message kind, so a branch whose software predates a separate
    # kind for it still gets it. See requisition_service.apply_update.
    if state.get("kind") == "requisition":
        from app.services import requisition_service

        return await requisition_service.apply_update(state.get("requisition") or {})
    transfer = await Transfer.get_or_none(id=state.get("id"), direction="outbound").prefetch_related("lines")
    if not transfer:
        created = await _outbound_from_head_office(state)
        return "created" if created else "not-here"
    if transfer.status in ("received", "received_short") and state.get("status") not in ("received", "received_short"):
        return "older-than-receipt"
    old_status, old_ack = transfer.status, transfer.ack_status
    incoming = state.get("status") or transfer.status
    if _moves_forward(transfer.status, incoming):
        transfer.status = incoming
    for key, value in _take_answer(None, state).items():
        setattr(transfer, key, value)
    transfer.received_at = _dt(state.get("receivedAt")) or transfer.received_at
    transfer.received_by_name = state.get("receivedBy") or transfer.received_by_name
    transfer.dispute_open = bool(state.get("disputeOpen"))
    transfer.dispute_note = state.get("disputeNote")
    await transfer.save()
    by_sku = {str(l.get("sku")): l.get("qtyReceived") for l in state.get("lines") or []}
    for line in transfer.lines:
        received = by_sku.get(str(line.sku))
        if received is not None:
            line.qty_received = Decimal(str(received))
            await line.save(update_fields=["qty_received"])

    requester = [str(transfer.requested_by_id)] if transfer.requested_by_id else []
    if transfer.ack_status != old_ack and transfer.ack_status in ("acknowledged", "overridden"):
        await _notify("transfer.agreed", transfer, f"{transfer.from_warehouse} agreed to {transfer.number}" if transfer.ack_status == "acknowledged"
                      else f"Head office cleared {transfer.number} to go without {transfer.from_warehouse}'s answer",
                      "Dispatch it when it's loaded." if transfer.ack_status == "acknowledged" else transfer.override_reason, SENDERS, requester, tone="good")
    if transfer.ack_status != old_ack and transfer.ack_status == "declined":
        await _notify("transfer.declined", transfer, f"{transfer.from_warehouse} declined {transfer.number}", transfer.ack_note, SENDERS, requester, tone="bad")
    if transfer.status in ("received", "received_short") and old_status not in ("received", "received_short"):
        short = transfer.dispute_open
        await _notify("transfer.received", transfer, f"{transfer.from_warehouse} received {transfer.number}" + (" and opened a dispute" if short else ""),
                      transfer.dispute_note if short else f"Signed for by {transfer.received_by_name or 'the branch'}.", SENDERS, requester, tone="bad" if short else "good")
    if transfer.status == "cancelled" and old_status != "cancelled":
        await _notify("transfer.cancelled", transfer, f"{transfer.number} to {transfer.from_warehouse} was cancelled", transfer.notes, SENDERS, requester, tone="warning")
    return "updated"


async def replace_known_branches(entries: list[dict]) -> None:
    codes = {str(e.get("code")).upper() for e in entries if e.get("code")}
    await KnownBranch.exclude(code__in=list(codes)).delete()
    for entry in entries:
        code = str(entry.get("code") or "").upper()
        if not code:
            continue
        existing = await KnownBranch.get_or_none(code=code)
        if existing:
            if existing.name != entry.get("name") or existing.city != entry.get("city"):
                existing.name, existing.city = entry.get("name") or code, entry.get("city")
                await existing.save()
        else:
            await KnownBranch.create(code=code, name=entry.get("name") or code, city=entry.get("city"))
