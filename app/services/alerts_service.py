"""What each person at this branch must do now, and what they should know.

**Tasks** ("needs your action") are worked out from the records every time they're asked for: a shipment
waiting for this branch's go-ahead, one on its way to be received, an order waiting for approval. A task is
shown to everyone who holds the ability to act on it, and it disappears the moment somebody does — there is
nothing to tick off and nothing that can be forgotten. A task that has waited too long is marked overdue.

**Notices** ("for your information") are stored when something happens — a shipment left, head office sent
one without waiting, an order was approved — and each person marks them read.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.pk_time import day_start, pk_time
from app.models import Adjustment, Notice, NoticeRead, PhysicalCount, PurchaseOrder, Transfer, User
from app.services.rbac_service import grants_of

NOTICE_DAYS = 30
NOTICE_LIMIT = 60

# How long a step may wait before it's marked overdue.
ACK_OVERDUE = timedelta(hours=2)
RECEIVE_OVERDUE = timedelta(hours=24)
DISPATCH_OVERDUE = timedelta(hours=4)
APPROVAL_OVERDUE = timedelta(hours=24)

READY_TO_SEND = ("acknowledged", "skipped", "overridden")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _task(key: str, group: str, title: str, detail: str, link: str, since: datetime | None, overdue_after: timedelta) -> dict:
    since = _aware(since) or _now()
    return {
        "key": key, "group": group, "title": title, "detail": detail, "link": link,
        "since": since.isoformat(), "overdue": _now() - since >= overdue_after,
    }


def _items(n: int) -> str:
    return f"{n} Item{'' if n == 1 else 's'}"


async def notify(
    kind: str, title: str, *, body: str | None = None, link: str | None = None, tone: str = "info",
    audience_any: list[tuple[str, str]] | None = None, users: list[str] | None = None,
    subject: tuple[str, str] | None = None,
) -> Notice:
    return await Notice.create(
        kind=kind, title=title[:200], body=(body or None) and body[:500], link=link, tone=tone,
        subject_type=subject[0] if subject else None, subject_id=subject[1] if subject else None,
        audience={"any": [list(pair) for pair in (audience_any or [])], "users": [str(u) for u in (users or [])]},
    )


def _for(user: User, grants: dict[str, set[str]], audience: dict) -> bool:
    if str(user.id) in (audience or {}).get("users", []):
        return True
    return any(action in grants.get(resource, set()) for resource, action in (audience or {}).get("any", []))


# ── tasks ──────────────────────────────────────────────────────────────────────────────────────

async def _shipments(can) -> list[dict]:
    out: list[dict] = []
    if can("inventory.transfers", "W"):
        for t in await Transfer.filter(direction="inbound", ack_status="awaiting", status__in=["approved", "requested"]).prefetch_related("lines"):
            out.append(_task(
                f"transfer:{t.id}:ack", "Shipments", f"{t.from_warehouse} wants to send {t.number or 'a shipment'}",
                f"{_items(len(t.lines))}. Say whether it can be sent: agree, or decline with a reason.",
                "/inventory/transfers", t.ack_requested_at or t.requested_at, ACK_OVERDUE,
            ))
        # Old demo records (origin "local") never came from anyone, so nobody is reminded about them.
        for t in await Transfer.filter(direction="inbound", status__in=["dispatched", "in_transit"]).exclude(origin="local").prefetch_related("lines"):
            out.append(_task(
                f"transfer:{t.id}:receive", "Shipments", f"Receive {t.number or 'the shipment'} from {t.from_warehouse}",
                f"{_items(len(t.lines))} on the way{f' ({t.vehicle})' if t.vehicle else ''}. Count it in when it arrives, then accept it or open a dispute.",
                "/inventory/transfers", t.dispatched_at or t.requested_at, RECEIVE_OVERDUE,
            ))
    if can("inventory.transfers.hold", "X"):
        for t in await Transfer.filter(direction="inbound", status="held").exclude(origin="local"):
            out.append(_task(
                f"transfer:{t.id}:held", "Shipments", f"{t.number or 'A shipment'} is on hold",
                f"Held: {t.hold_reason}. Release it to receive it, or settle it with {t.from_warehouse}.",
                "/inventory/transfers", t.held_at, RECEIVE_OVERDUE,
            ))
    if can("inventory.transfers.approve", "X"):
        for t in await Transfer.filter(direction="outbound", status="awaiting_approval").prefetch_related("lines"):
            out.append(_task(
                f"transfer:{t.id}:approve", "Shipments", f"Approve sending {t.number} to {t.from_warehouse}",
                f"{_items(len(t.lines))} asked for. Nothing leaves until it's approved.", "/inventory/transfers", t.requested_at, APPROVAL_OVERDUE,
            ))
    if can("inventory.transfers.send", "W"):
        for t in await Transfer.filter(direction="outbound", status="approved", ack_status__in=list(READY_TO_SEND)).prefetch_related("lines"):
            out.append(_task(
                f"transfer:{t.id}:dispatch", "Shipments", f"Dispatch {t.number} to {t.from_warehouse}",
                f"{t.from_warehouse} agreed. {_items(len(t.lines))}. Load it and mark it dispatched.",
                "/inventory/transfers", t.ack_at or t.requested_at, DISPATCH_OVERDUE,
            ))
        for t in await Transfer.filter(direction="outbound", ack_status="declined", status__in=["requested", "approved"]):
            out.append(_task(
                f"transfer:{t.id}:declined", "Shipments", f"{t.from_warehouse} declined {t.number}",
                f"“{t.ack_note or 'No reason given'}”. Cancel it, or talk to them and send again.",
                "/inventory/transfers", t.ack_at, APPROVAL_OVERDUE,
            ))
    return out


async def _purchasing(user: User, can) -> list[dict]:
    out: list[dict] = []
    if can("inventory.purchase-orders.approve", "X"):
        for po in await PurchaseOrder.filter(status="draft").exclude(created_by_id=user.id).prefetch_related("supplier", "lines"):
            out.append(_task(
                f"po:{po.id}:approve", "Purchasing", f"Approve {po.po_number} to {po.supplier.name}",
                f"{_items(len(po.lines))} ordered. The supplier isn't asked until it's approved.", "/inventory/purchase-orders", po.created_at, APPROVAL_OVERDUE,
            ))
    if can("inventory.receiving", "W"):
        late = await PurchaseOrder.filter(status__in=["approved", "partially-received"], expected_at__lt=_now()).prefetch_related("supplier")
        for po in late:
            out.append(_task(
                f"po:{po.id}:late", "Purchasing", f"{po.po_number} from {po.supplier.name} is late",
                f"Expected {pk_time(po.expected_at):%d %b}. Receive what came, or chase the supplier.", "/inventory/receiving", po.expected_at, timedelta(0),
            ))
    return out


async def _corrections(user: User, can) -> list[dict]:
    out: list[dict] = []
    if can("inventory.counts.approve", "X"):
        pending = await PhysicalCount.filter(status="pending").exclude(counted_by_id=user.id).order_by("at")
        if pending:
            out.append(_task(
                "counts:approve", "Stock", f"{len(pending)} stock count{'' if len(pending) == 1 else 's'} to approve",
                "Counted by someone else and waiting for a decision.", "/branch-console/approvals", pending[0].at, APPROVAL_OVERDUE,
            ))
    if can("inventory.adjustments.approve", "X"):
        pending = await Adjustment.filter(status="pending").exclude(submitted_by_id=user.id).order_by("at")
        if pending:
            out.append(_task(
                "adjustments:approve", "Stock", f"{len(pending)} stock adjustment{'' if len(pending) == 1 else 's'} to approve",
                "Damage, expiry or loss reported by someone else.", "/branch-console/approvals", pending[0].at, APPROVAL_OVERDUE,
            ))
    return out


async def _accounts(user: User, can) -> list[dict]:
    from datetime import date as _date

    from app.models import Cheque, Voucher
    from app.services.accounts_reports_service import shop_day

    out: list[dict] = []
    if can("accounts.vouchers.post", "X"):
        drafts = await Voucher.filter(status="draft", auto=False).order_by("created_at")
        if drafts:
            out.append(_task(
                "vouchers:post", "Accounts", f"{len(drafts)} voucher{'' if len(drafts) == 1 else 's'} waiting to be posted",
                "Saved as drafts. They aren't in the books until someone posts them.", "/accounts/vouchers?status=draft", drafts[0].created_at, APPROVAL_OVERDUE,
            ))
    if can("accounts.cheques", "W"):
        due = await Cheque.filter(status="pending", cheque_date__lte=shop_day()).order_by("cheque_date")
        if due:
            since = day_start(due[0].cheque_date) if isinstance(due[0].cheque_date, _date) else None
            out.append(_task(
                "cheques:deposit", "Accounts", f"{len(due)} cheque{'' if len(due) == 1 else 's'} due to deposit",
                "Their date has come. Deposit them and mark them cleared, or bounced.", "/accounts/cheques", since, timedelta(days=2),
            ))
    if can("accounts.period", "X"):
        from app.services.vouchers_service import settings as accounts_settings

        row = await accounts_settings()
        if row.posting_problems:
            out.append(_task(
                "accounts:posting", "Accounts", "Some records couldn't be posted to the books",
                str(row.posting_problems[0])[:200], "/accounts/settings", row.last_posting_at, timedelta(hours=4),
            ))
    return out


async def tasks_for(user: User, grants: dict[str, set[str]] | None = None) -> list[dict]:
    grants = grants if grants is not None else await grants_of(user)

    def can(resource: str, action: str) -> bool:
        return action in grants.get(resource, set())

    tasks = await _shipments(can) + await _purchasing(user, can) + await _corrections(user, can) + await _accounts(user, can)
    # Overdue first, then oldest first.
    return sorted(tasks, key=lambda t: (not t["overdue"], t["since"]))


# ── notices ────────────────────────────────────────────────────────────────────────────────────

async def notices_for(user: User, grants: dict[str, set[str]] | None = None) -> list[dict]:
    grants = grants if grants is not None else await grants_of(user)
    since = _now() - timedelta(days=NOTICE_DAYS)
    rows = await Notice.filter(at__gte=since).order_by("-at").limit(400)
    mine = [n for n in rows if _for(user, grants, n.audience)][:NOTICE_LIMIT]
    read = set(await NoticeRead.filter(user=user, notice_id__in=[n.id for n in mine]).values_list("notice_id", flat=True))
    return [{
        "id": str(n.id), "at": _aware(n.at).isoformat(), "kind": n.kind, "title": n.title, "body": n.body,
        "link": n.link, "tone": n.tone, "read": n.id in read,
    } for n in mine]


async def summary(user: User) -> dict:
    grants = await grants_of(user)
    tasks = await tasks_for(user, grants)
    notices = await notices_for(user, grants)
    return {"tasks": tasks, "notices": notices, "unread": sum(1 for n in notices if not n["read"])}


async def mark_read(user: User, ids: list[str] | None) -> int:
    grants = await grants_of(user)
    visible = {n["id"] for n in await notices_for(user, grants) if not n["read"]}
    wanted = visible if ids is None else visible & {str(i) for i in ids}
    for notice_id in wanted:
        await NoticeRead.get_or_create(notice_id=notice_id, user=user)
    return len(wanted)

