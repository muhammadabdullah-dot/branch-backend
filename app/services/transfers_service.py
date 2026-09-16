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

A branch that stays offline for a long time can be sent to without its acknowledgement: head office's
Warehouse Manager does that with a written reason, and this branch is told.
"""
from datetime import datetime, timezone
from decimal import Decimal

from tortoise.transactions import atomic

from app.core.device_context import get_device_id
from app.models import KnownBranch, Location, OutboxEvent, Product, ProductAlias, StockMovement, Transfer, TransferLine, User, balance_for, next_value

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
        raise TransferError("Say why it shouldn't be sent — the sender needs to know what to change.")
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
    if not (note or "").strip():
        raise TransferError("Say what's wrong with it.")
    transfer.dispute_open = True
    transfer.dispute_note = note.strip()
    await transfer.save()
    if transfer.origin != "local":
        await _event(user, transfer, {"event": "dispute_opened", "transferId": str(transfer.id), "note": transfer.dispute_note, "by": user.name})
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
            raise TransferError(f"Enter what arrived of {line.product.name} — 0 if none did.")
        if qty < ZERO or qty > line.qty_sent:
            raise TransferError(f"{line.product.name}: received must be between 0 and the {line.qty_sent.normalize():f} sent.")
        line.qty_received = qty
        await line.save(update_fields=["qty_received"])
        if qty < line.qty_sent:
            short = True
        if qty > ZERO:
            # Arrives at what it cost the sender, and averages in with what's already on the shelf.
            if line.unit_cost is not None:
                product = line.product
                on_hand = await balance_for(product.id)
                if on_hand > ZERO and product.avg_cost:
                    product.avg_cost = (product.avg_cost * on_hand + qty * line.unit_cost) / (on_hand + qty)
                else:
                    product.avg_cost = line.unit_cost
                await product.save(update_fields=["avg_cost"])
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
        transfer.dispute_note = (note or "").strip() or "Short receipt — less arrived than was sent."
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
        raise TransferError(f"{transfer.number or 'This shipment'} can't be put on hold — it's {transfer.status.replace('_', ' ')}.")
    if not (reason or "").strip():
        raise TransferError("Say why it's being held — damaged, wrong goods, not expected…")
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

    now = _now()
    seq = await next_value("transfer_out", 1)
    can_approve = await has_permission(user, "inventory.transfers.approve", "X")
    transfer = await Transfer.create(
        number=f"{identity.code}-TR-{seq:04d}", direction="outbound", origin="branch",
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
        "lines": [{
            "sku": line.product.sku, "name": line.product.name, "unit": line.product.unit, "price": str(line.product.price),
            "taxRate": str(line.product.tax_rate), "isWeighed": line.product.is_weighed, "qtySent": str(line.qty_sent),
            "unitCost": str(line.unit_cost if line.unit_cost is not None else line.product.avg_cost or 0),
        } for line in transfer.lines],
    }})


@atomic()
async def approve_outbound(user: User, transfer_id: str) -> Transfer:
    transfer = await Transfer.get_or_none(id=transfer_id, direction="outbound")
    if not transfer:
        raise TransferError("That outgoing shipment doesn't exist.")
    if transfer.status != "awaiting_approval":
        raise TransferError(f"{transfer.number} isn't waiting for approval — it's {transfer.status.replace('_', ' ')}.")
    await _ask_destination(user, transfer, approved_by=user)
    await transfer.fetch_related("lines")
    return transfer


@atomic()
async def dispatch_ready(user: User, transfer_id: str, vehicle: str | None, driver: str | None) -> Transfer:
    """The other branch agreed: the stock leaves its location now, and head office relays it."""
    transfer = await Transfer.get_or_none(id=transfer_id, direction="outbound")
    if not transfer:
        raise TransferError("That outgoing shipment doesn't exist.")
    if transfer.status != "approved" or transfer.ack_status not in READY_TO_SEND:
        if transfer.ack_status == "awaiting":
            raise TransferError(f"{transfer.from_warehouse} hasn't agreed to receive {transfer.number} yet.")
        if transfer.ack_status == "declined":
            raise TransferError(f"{transfer.from_warehouse} declined {transfer.number}: {transfer.ack_note or 'no reason given'}.")
        raise TransferError(f"{transfer.number} can't be dispatched — it's {transfer.status.replace('_', ' ')}.")
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
        "lines": [{
            "sku": line.product.sku, "name": line.product.name, "unit": line.product.unit, "price": str(line.product.price),
            "taxRate": str(line.product.tax_rate), "isWeighed": line.product.is_weighed, "qtySent": str(line.qty_sent),
            "unitCost": str(line.unit_cost if line.unit_cost is not None else line.product.avg_cost or 0),
        } for line in transfer.lines],
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
        raise TransferError(f"{transfer.number} has already left — it can't be cancelled.")
    if transfer.requested_by_id != user.id and not await has_permission(user, "inventory.transfers.approve", "X"):
        raise TransferError("Only the person who asked, or someone who approves shipments, can cancel it.")
    told_head_office = transfer.status != "awaiting_approval"
    transfer.status = "cancelled"
    transfer.notes = ((transfer.notes or "") + (f" — cancelled: {reason.strip()}" if reason and reason.strip() else " — cancelled"))[:255]
    await transfer.save()
    if told_head_office:
        await _event(user, transfer, {"event": "cancelled", "transferId": str(transfer.id), "reason": (reason or "").strip() or None, "by": user.name})
    await transfer.fetch_related("lines")
    return transfer


# ── applying what head office sends ─────────────────────────────────────────────────────────────

async def _product_for(line: dict) -> Product:
    """This branch's Item for a line's code — by code, barcode or alternate barcode — or a new Item made
    from the line when head office sends something this branch has never stocked."""
    sku = str(line.get("sku") or "").strip()
    if not sku:
        raise TransferError("A transfer line from head office has no Item code.")
    product = await Product.get_or_none(sku=sku) or await Product.get_or_none(barcode=sku)
    if not product:
        alias = await ProductAlias.get_or_none(code=sku).prefetch_related("product")
        product = alias.product if alias else None
    if product:
        return product
    product_id = sku if not await Product.exists(id=sku) else f"ho-{sku}"
    return await Product.create(
        id=product_id[:40], sku=sku[:40], name=(line.get("name") or sku)[:160], price=Decimal(str(line.get("price") or "0")),
        tax_rate=Decimal(str(line.get("taxRate") or "0")), unit=(line.get("unit") or "pc")[:20],
        is_weighed=bool(line.get("isWeighed")), avg_cost=Decimal(str(line.get("unitCost") or "0")), remarks="Added by a head office transfer",
    )


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
    if transfer and not _moves_forward(transfer.status, incoming_status):
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
        product = await _product_for(line)
        await TransferLine.create(
            transfer=transfer, product=product, sku=line.get("sku"), qty_sent=Decimal(str(line.get("qtySent") or "0")),
            unit_cost=Decimal(str(line["unitCost"])) if line.get("unitCost") not in (None, "") else None,
        )

    # Tell people here what changed. (What they must do next shows as a task by itself.)
    old_status = before[0] if before else None
    if transfer.status == "dispatched" and old_status != "dispatched" and old_status != "held":
        await _notify("transfer.dispatched", transfer, f"{_label(transfer)} is on its way from {transfer.from_warehouse}",
                      f"Vehicle {transfer.vehicle or '—'}, driver {transfer.driver or '—'}. Count it in when it arrives.", RECEIVERS)
    if transfer.override_reason and (not before or not before[2]):
        await _notify("transfer.sent_without_answer", transfer, f"{transfer.from_warehouse} is sending {_label(transfer)} without this branch's go-ahead",
                      f"{transfer.override_by_name or 'Head office'}: “{transfer.override_reason}”", RECEIVERS, tone="warning")
    if transfer.status == "cancelled" and old_status not in (None, "cancelled"):
        await _notify("transfer.cancelled", transfer, f"{_label(transfer)} from {transfer.from_warehouse} was cancelled", transfer.notes, RECEIVERS, tone="warning")
    return outcome


@atomic()
async def apply_outbound(state: dict) -> str:
    transfer = await Transfer.get_or_none(id=state.get("id"), direction="outbound").prefetch_related("lines")
    if not transfer:
        return "not-here"
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
