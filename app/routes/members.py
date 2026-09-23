"""D.Marina members and the loyalty points rules, on the branch server.

The till looks members up (store.billing); the Members screen finds, adds and corrects them
(branch-console.members); the rules are set under branch-console.loyalty — or at head office.
"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.middlewares.auth import require_any_permission, require_permission
from app.models import LoyaltyEntry, LoyaltySettings, Member, User
from app.schemas.types import Money, Percent
from app.services import members_service

router = APIRouter(tags=["members"])

_read = require_any_permission(("store.billing", "R"), ("branch-console.members", "R"), ("store.returns", "R"))
_write = require_permission("branch-console.members", "W")
_settings_read = require_any_permission(("store.billing", "R"), ("branch-console.members", "R"), ("branch-console.loyalty", "R"))
_settings_write = require_permission("branch-console.loyalty", "W")


class MemberOut(BaseModel):
    id: str
    code: str
    name: str
    phone: str
    joinedVia: str
    joinedMethod: str | None = None
    homeBranchCode: str
    partyId: str | None = None
    partyName: str | None = None
    points: int
    active: bool
    createdBy: str | None = None
    createdAt: datetime


class MemberListOut(BaseModel):
    items: list[MemberOut]
    total: int


class LoyaltyEntryOut(BaseModel):
    id: str
    kind: str
    points: int
    invoiceNumber: str | None = None
    branchCode: str
    note: str | None = None
    by: str | None = None
    at: datetime


class MemberDetailOut(MemberOut):
    entries: list[LoyaltyEntryOut]


class MemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=1, max_length=30)
    partyId: str | None = None


class MemberUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    partyId: str | None = None
    active: bool | None = None


class LoyaltySettingsOut(BaseModel):
    enabled: bool
    rupeesPerPoint: Money
    pointValue: Money
    minRedeemPoints: int
    maxRedeemPercent: Percent
    updatedAt: datetime | None = None
    updatedBy: str | None = None
    updatedFrom: str | None = None


class LoyaltySettingsIn(BaseModel):
    enabled: bool | None = None
    rupeesPerPoint: Decimal | None = Field(default=None, gt=0)
    pointValue: Decimal | None = Field(default=None, gt=0)
    minRedeemPoints: int | None = Field(default=None, ge=0)
    maxRedeemPercent: Decimal | None = Field(default=None, gt=0, le=100)


def _fail(exc: members_service.MemberError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def _member_out(m: Member) -> MemberOut:
    party = m.party if m.party_id else None
    return MemberOut(
        id=str(m.id), code=m.code, name=m.name, phone=m.phone, joinedVia=m.joined_via, joinedMethod=m.joined_method,
        homeBranchCode=m.home_branch_code, partyId=str(m.party_id) if m.party_id else None,
        partyName=party.name if party else None, points=m.points_balance, active=m.active,
        createdBy=m.created_by_name, createdAt=m.created_at,
    )


def _entry_out(e: LoyaltyEntry) -> LoyaltyEntryOut:
    return LoyaltyEntryOut(
        id=str(e.id), kind=e.kind, points=e.points, invoiceNumber=e.invoice_number, branchCode=e.branch_code,
        note=e.note, by=e.by_name, at=e.at,
    )


def _settings_out(s: LoyaltySettings) -> LoyaltySettingsOut:
    return LoyaltySettingsOut(
        enabled=s.enabled, rupeesPerPoint=s.rupees_per_point, pointValue=s.point_value, minRedeemPoints=s.min_redeem_points,
        maxRedeemPercent=s.max_redeem_percent, updatedAt=s.updated_at, updatedBy=s.updated_by_name, updatedFrom=s.updated_from,
    )


async def _detail(m: Member) -> MemberDetailOut:
    return MemberDetailOut(**_member_out(m).model_dump(), entries=[_entry_out(e) for e in await members_service.entries(m)])


@router.get("/members", response_model=MemberListOut)
async def list_members(
    q: str | None = None, limit: int = 50, offset: int = 0, sort: str | None = None, order: str | None = None,
    user: User = Depends(_read),
) -> MemberListOut:
    """Search by member code, name or mobile number; newest members first unless `sort` names a column (see
    members_service.SORTS) and `order` is asc or desc."""
    items, total = await members_service.search(q, min(max(limit, 1), 200), max(offset, 0), sort, order)
    return MemberListOut(items=[_member_out(m) for m in items], total=total)


@router.get("/members/lookup", response_model=MemberOut | None)
async def lookup(q: str, user: User = Depends(_read)) -> MemberOut | None:
    """The member a till entry means — their code or their mobile number — or nothing."""
    member = await members_service.lookup(q)
    return _member_out(member) if member else None


@router.post("/members", response_model=MemberDetailOut)
async def create_member(payload: MemberCreate, user: User = Depends(_write)) -> MemberDetailOut:
    try:
        member = await members_service.create_from_screen(payload.name, payload.phone, payload.partyId, user)
    except members_service.MemberError as exc:
        raise _fail(exc)
    return await _detail(member)


@router.get("/members/{code}", response_model=MemberDetailOut)
async def member_detail(code: str, user: User = Depends(_read)) -> MemberDetailOut:
    try:
        return await _detail(await members_service.get(code))
    except members_service.MemberError as exc:
        raise _fail(exc)


@router.patch("/members/{code}", response_model=MemberDetailOut)
async def update_member(code: str, payload: MemberUpdate, user: User = Depends(_write)) -> MemberDetailOut:
    try:
        member = await members_service.update(code, payload.model_dump(exclude_unset=True), user)
    except members_service.MemberError as exc:
        raise _fail(exc)
    return await _detail(member)


@router.get("/loyalty/settings", response_model=LoyaltySettingsOut)
async def get_settings(user: User = Depends(_settings_read)) -> LoyaltySettingsOut:
    return _settings_out(await members_service.settings())


@router.put("/loyalty/settings", response_model=LoyaltySettingsOut)
async def put_settings(payload: LoyaltySettingsIn, user: User = Depends(_settings_write)) -> LoyaltySettingsOut:
    try:
        return _settings_out(await members_service.update_settings(payload.model_dump(exclude_unset=True), user))
    except members_service.MemberError as exc:
        raise _fail(exc)
