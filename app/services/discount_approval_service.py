"""A manager's approval of a discount above salesperson authority.

POST /sales used to take the approving manager's user id on trust. It checked that the id belonged to
an active user holding `store.discount-override` X, and never that this user had approved anything:
the till signed the manager in on its own screen and sent only their id. Any salesperson who knew a
manager's id could post a sale with whatever discount they liked, no password involved.

An approval is now something this server issues and signs, after it has checked the manager's
password: or, for a salesperson who holds the authority themselves, their own session. The signed
approval names who approved, the most total discount they approved, the salesperson who asked, and
the one bill it is for. POST /sales accepts nothing less, and records the approver it names.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import jwt

from app.core.security import DISCOUNT_APPROVAL_TTL, create_discount_approval_token, decode_discount_approval_token
from app.models import User
from app.services import auth_service
from app.services.rbac_service import discount_limit_of, has_permission

OVERRIDE_RESOURCE = "store.discount-override"
# Every percent here is a share of the bill's margin (what it sells for less what its Items cost the shop, tax included),
# never of its price: see services/sale_rules.py. However high the approval, no bill sells below that cost.
# Floating-point slack between the percent the till solved for and the one the server recomputes from
# it. A ten-thousandth of a percentage point is one rupee on a million-rupee bill.
PERCENT_TOLERANCE = Decimal("0.0001")


class ApprovalError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


@dataclass
class Approval:
    token: str
    approver: User
    max_percent: Decimal
    expires_at: datetime


async def issue(
    requester: User, bill_id: str, max_percent: Decimal, email: str | None, password: str | None
) -> Approval:
    if email and email.strip():
        if not password:
            raise ApprovalError("Enter the manager's password")
        approver = await auth_service.authenticate(email, password)
        if not approver:
            # 400, not 401: the till reads a 401 as its own salesperson's session ending.
            raise ApprovalError("Invalid email or password")
        if not await has_permission(approver, OVERRIDE_RESOURCE, "X"):
            raise ApprovalError(f"{approver.name} can't approve discounts.", status=403)
    else:
        approver = requester
        if not await has_permission(approver, OVERRIDE_RESOURCE, "X"):
            raise ApprovalError("You can't approve discounts. Someone who can needs to sign in and approve this", status=403)
    # Nobody approves more discount than they may give themselves.
    limit = discount_limit_of(approver)
    if max_percent > limit + PERCENT_TOLERANCE:
        raise ApprovalError(
            f"{approver.name} can approve discounts up to {limit.normalize():f}% of a bill's profit, but this bill needs {max_percent:.1f}%.", status=403,
        )
    return grant(approver, requester, bill_id, max_percent)


def grant(approver: User, requester: User, bill_id: str, max_percent: Decimal) -> Approval:
    """Signs the approval without checking anyone's password or authority: `issue` does that first.
    Only for server-side callers that already know who approved (the demo-data script); verify()
    still checks the approver's authority when the sale lands."""
    # Whole seconds, so the expiry reported back matches the one inside the token.
    now = datetime.now(timezone.utc).replace(microsecond=0)
    token = create_discount_approval_token(
        {
            "approver": str(approver.id),
            "requester": str(requester.id),
            "bill": bill_id,
            "maxPct": format(max_percent, "f"),
        },
        now,
    )
    return Approval(token=token, approver=approver, max_percent=max_percent, expires_at=now + DISCOUNT_APPROVAL_TTL)


async def verify(token: str, cashier: User, bill_id: str | None, effective_pct: Decimal) -> User:
    """The approver, once the approval is proven to be this server's, unexpired, and for this
    salesperson, this bill and at least this much discount. Their authority is checked again here:
    a manager whose authority was withdrawn after approving no longer approves anything."""
    try:
        claims = decode_discount_approval_token(token)
    except jwt.ExpiredSignatureError:
        raise ApprovalError("The manager's approval for this discount has expired. Ask them to approve it again")
    except jwt.PyJWTError:
        raise ApprovalError("This discount approval isn't valid. Ask a manager to approve it again")
    try:
        approver_id = str(claims["approver"])
        requester_id = str(claims["requester"])
        approved_bill = str(claims["bill"])
        max_pct = Decimal(str(claims["maxPct"]))
    except (KeyError, InvalidOperation):
        raise ApprovalError("This discount approval isn't valid. Ask a manager to approve it again")

    if requester_id != str(cashier.id):
        raise ApprovalError("This discount was approved for another salesperson. Ask a manager to approve it for you")
    # The bill is its idempotency key, which only one sale can ever commit under: so an approval
    # covers one sale, not every bill rung up in the next fifteen minutes.
    if not bill_id or approved_bill != bill_id:
        raise ApprovalError("This discount was approved for a different bill. Ask a manager to approve this one")
    if effective_pct > max_pct + PERCENT_TOLERANCE:
        raise ApprovalError(
            f"This discount is {effective_pct:.2f}% of the bill's profit, more than the {max_pct:.2f}% the manager approved, so ask them to approve it again"
        )

    approver = await User.get_or_none(id=approver_id, active=True).prefetch_related("role")
    if not approver or not await has_permission(approver, OVERRIDE_RESOURCE, "X"):
        raise ApprovalError("The person who approved this discount can't approve discounts any more. Ask again")
    if effective_pct > discount_limit_of(approver) + PERCENT_TOLERANCE:
        raise ApprovalError(f"{approver.name} can approve discounts up to {discount_limit_of(approver).normalize():f}% of a bill's profit, so ask someone with a higher limit")
    return approver
