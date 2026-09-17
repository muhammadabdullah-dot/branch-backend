from datetime import datetime, timezone
from decimal import Decimal

from tortoise.transactions import atomic

from app.models import Location, Product, PurchaseOrder, PurchaseOrderLine, Supplier, User, next_value
from app.schemas.purchase_orders import PurchaseOrderCreate, PurchaseOrderLineIn, PurchaseOrderUpdate
from app.services.inventory_service import in_window

APPROVE_RESOURCE = "inventory.purchase-orders.approve"

OPEN_FOR_RECEIVING = ("approved", "partially-received")


class PurchaseOrderError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


async def _with_lines(po: PurchaseOrder) -> PurchaseOrder:
    await po.fetch_related("lines__product", "supplier", "created_by", "approved_by", "grns")
    return po


async def get(po_id: str) -> PurchaseOrder:
    po = await PurchaseOrder.get_or_none(id=po_id)
    if not po:
        raise PurchaseOrderError("Purchase order not found", status=404)
    return await _with_lines(po)


async def list_all(
    limit: int, offset: int, from_at: datetime | None, to_at: datetime | None,
    status: str | None = None, supplier_id: str | None = None,
) -> tuple[list[PurchaseOrder], int]:
    qs = in_window(PurchaseOrder.all(), "created_at", from_at, to_at)
    if status == "open":
        qs = qs.filter(status__in=OPEN_FOR_RECEIVING)
    elif status:
        qs = qs.filter(status=status)
    if supplier_id:
        qs = qs.filter(supplier_id=supplier_id)
    total = await qs.count()
    orders = await qs.order_by("-created_at").offset(offset).limit(limit)
    return [await _with_lines(po) for po in orders], total


async def _check_header(supplier_id: str, location_id: str) -> tuple[Supplier, Location]:
    supplier = await Supplier.get_or_none(id=supplier_id)
    if not supplier:
        raise PurchaseOrderError(f"Unknown supplier {supplier_id}")
    if not supplier.active:
        raise PurchaseOrderError(f"{supplier.name} is switched off. Switch it back on to order from them.")
    location = await Location.get_or_none(id=location_id)
    if not location or not location.active:
        raise PurchaseOrderError("Pick an active location for the delivery.")
    return supplier, location


async def _write_lines(po: PurchaseOrder, lines: list[PurchaseOrderLineIn]) -> None:
    if not lines:
        raise PurchaseOrderError("A purchase order needs at least one line.")
    ids = [line.productId for line in lines]
    if len(set(ids)) != len(ids):
        raise PurchaseOrderError("An Item is on the order twice. Put the whole quantity on one line.")
    products = {p.id: p for p in await Product.filter(id__in=ids)}
    missing = [i for i in ids if i not in products]
    if missing:
        raise PurchaseOrderError(f"Unknown Item {missing[0]}")
    await PurchaseOrderLine.filter(purchase_order_id=po.id).delete()
    for position, line in enumerate(lines):
        await PurchaseOrderLine.create(
            purchase_order=po, product=products[line.productId], qty=line.qty,
            unit_price=line.unitPrice, disc_percent=line.discPercent, position=position,
        )


@atomic()
async def create(user: User, data: PurchaseOrderCreate) -> PurchaseOrder:
    supplier, location = await _check_header(data.supplierId, data.locationId)
    seq = await next_value("purchase_order", 1)
    po = await PurchaseOrder.create(
        po_number=f"PO-{seq:04d}", supplier=supplier, location=location, status="draft",
        expected_at=data.expectedAt, notes=(data.notes or "").strip() or None, created_by=user,
    )
    await _write_lines(po, data.lines)
    return await _with_lines(po)


async def _load_for_change(po_id: str) -> PurchaseOrder:
    po = await PurchaseOrder.get_or_none(id=po_id)
    if not po:
        raise PurchaseOrderError("Purchase order not found", status=404)
    return po


@atomic()
async def update(user: User, po_id: str, data: PurchaseOrderUpdate) -> PurchaseOrder:
    po = await _load_for_change(po_id)
    if po.status != "draft":
        raise PurchaseOrderError(f"{po.po_number} is {po.status}. Only a draft can be edited.")
    # Only the person who raised a draft changes it. Otherwise an approver could rewrite someone
    # else's order and then approve their own quantities — one person deciding the spend after all.
    if str(po.created_by_id) != str(user.id):
        await po.fetch_related("created_by")
        raise PurchaseOrderError(
            f"{po.po_number} was raised by {po.created_by.name}. Only they can change the draft, or you can cancel it and raise your own."
        )
    changes = data.model_dump(exclude_unset=True)
    supplier_id = changes.get("supplierId") or po.supplier_id
    location_id = changes.get("locationId") or po.location_id
    supplier, location = await _check_header(supplier_id, location_id)
    po.supplier, po.location = supplier, location
    if "expectedAt" in changes:
        po.expected_at = data.expectedAt
    if "notes" in changes:
        po.notes = (data.notes or "").strip() or None
    await po.save()
    if data.lines is not None:
        await _write_lines(po, data.lines)
    return await _with_lines(po)


@atomic()
async def approve(user: User, po_id: str) -> PurchaseOrder:
    po = await _load_for_change(po_id)
    if po.status != "draft":
        raise PurchaseOrderError(f"{po.po_number} is already {po.status}.")
    # The same rule as stock corrections: whoever raised the order doesn't sign it off. Otherwise an
    # order approval is one person spending the branch's money on their own say-so.
    if str(po.created_by_id) == str(user.id):
        raise PurchaseOrderError(f"You raised {po.po_number}, so someone else has to approve it.")
    if not await PurchaseOrderLine.filter(purchase_order_id=po.id).exists():
        raise PurchaseOrderError("A purchase order needs at least one line.")
    # The supplier or delivery location may have been switched off since the draft was raised.
    await _check_header(str(po.supplier_id), str(po.location_id))
    po.status = "approved"
    po.approved_by = user
    po.approved_at = datetime.now(timezone.utc)
    await po.save()
    from app.services import alerts_service

    await po.fetch_related("supplier")
    await alerts_service.notify(
        "po.approved", f"{po.po_number} to {po.supplier.name} approved by {user.name}", body="Send it to the supplier; receive it against the order when it arrives.",
        link="/inventory/purchase-orders", users=[str(po.created_by_id)], audience_any=[("inventory.receiving", "W")],
        subject=("purchase-order", str(po.id)), tone="good",
    )
    return await _with_lines(po)


@atomic()
async def cancel(user: User, po_id: str) -> PurchaseOrder:
    """A draft or an untouched approved order is cancelled. One already partly received is closed:
    what arrived stays received, and the rest is no longer expected.

    A Stock Keeper works under the Inventory Manager: they can withdraw their own draft, but an order
    that has been approved — or someone else's draft — is cancelled or closed only by someone who
    approves orders."""
    from app.services.rbac_service import has_permission

    po = await _load_for_change(po_id)
    if po.status in ("received", "cancelled", "closed"):
        raise PurchaseOrderError(f"{po.po_number} is already {po.status}.")
    own_draft = po.status == "draft" and str(po.created_by_id) == str(user.id)
    if not own_draft and not await has_permission(user, APPROVE_RESOURCE, "X"):
        if po.status == "draft":
            raise PurchaseOrderError(f"{po.po_number} is someone else's draft. Only they or an Inventory Manager can cancel it.")
        action = "close" if po.status == "partially-received" else "cancel"
        raise PurchaseOrderError(f"{po.po_number} has been approved, so only an Inventory Manager can {action} it.")
    po.status = "closed" if po.status == "partially-received" else "cancelled"
    po.closed_at = datetime.now(timezone.utc)
    await po.save()
    if str(po.created_by_id) != str(user.id):
        from app.services import alerts_service

        await alerts_service.notify(
            f"po.{po.status}", f"{po.po_number} {po.status} by {user.name}", link="/inventory/purchase-orders",
            users=[str(po.created_by_id)], subject=("purchase-order", str(po.id)), tone="warning",
        )
    return await _with_lines(po)


async def receive_against(po_id: str, supplier_id: str, received: dict[str, Decimal]) -> PurchaseOrder:
    """Ticks an order off with what a GRN received (paid-for units, bonus excluded). Runs inside the
    GRN's own transaction, so the order and the stock can't disagree."""
    po = await PurchaseOrder.get_or_none(id=po_id)
    if not po:
        raise PurchaseOrderError("That purchase order doesn't exist.")
    if po.status not in OPEN_FOR_RECEIVING:
        raise PurchaseOrderError(f"{po.po_number} is {po.status}, so nothing can be received against it.")
    if str(po.supplier_id) != str(supplier_id):
        raise PurchaseOrderError(f"{po.po_number} is with a different supplier.")
    lines = {str(line.product_id): line for line in await PurchaseOrderLine.filter(purchase_order_id=po.id).prefetch_related("product")}
    for product_id, qty in received.items():
        line = lines.get(product_id)
        if not line:
            product = await Product.get_or_none(id=product_id)
            raise PurchaseOrderError(f"{product.name if product else product_id} isn't on {po.po_number}. Receive it on a separate GRN.")
        remaining = line.qty - line.received_qty
        if qty > remaining:
            raise PurchaseOrderError(
                f"{po.po_number} has {remaining.normalize():f} of {line.product.name} left to receive, not {qty.normalize():f}. "
                "Put the extra down as bonus if it came free, or raise another order."
            )
    for product_id, qty in received.items():
        line = lines[product_id]
        line.received_qty = line.received_qty + qty
        await line.save(update_fields=["received_qty"])
    all_in = all(line.received_qty >= line.qty for line in lines.values())
    po.status = "received" if all_in else "partially-received"
    if all_in:
        po.closed_at = datetime.now(timezone.utc)
    await po.save()
    return po
