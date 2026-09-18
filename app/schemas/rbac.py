from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field

from app.schemas.auth import PermissionOut


class UserNameOut(BaseModel):
    """Just enough to render "who did this" on a ledger row — no email, role or permissions."""
    id: str
    name: str


class UserSummaryOut(BaseModel):
    id: str
    name: str
    email: str
    roleId: str
    active: bool
    title: str | None = None
    # The most bill discount they may give, and approve for others.
    discountLimit: str = "0"


class UpdatePermissionsRequest(BaseModel):
    permissions: list[PermissionOut]


class RoleOut(BaseModel):
    id: str
    name: str


class UserCreateRequest(BaseModel):
    """A Branch Manager taking on a new salesperson.

    The new account starts on its role's standard access and nothing else — the Branch Manager can
    widen or narrow it afterwards on the User Access tab. Granting the role's template up front is
    what makes a new starter able to sign in and work the same afternoon instead of waiting for
    somebody to tick twenty boxes.
    """

    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    # The starting point: cashier (Salesperson), pharmacist (Pharmacist) or branch-manager (everything).
    roleId: str
    title: str | None = Field(default=None, max_length=80)
    discountLimit: Decimal | None = Field(default=None, ge=0, le=100)


class UserUpdateRequest(BaseModel):
    """Everything optional — a PATCH touches only what it names. `roleId` is absent by design: a
    role change rewrites what someone may do, and doing that silently through an edit form is how
    a salesperson ends up able to approve their own discounts. Change access on the User Access
    tab, where the consequence is on screen. Switching between Salesperson and Pharmacist is its
    own request (`WorksAsRequest`)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: EmailStr | None = None
    active: bool | None = None
    # An admin reset, not a change-your-own-password flow — that needs the old one and has no
    # endpoint yet.
    password: str | None = Field(default=None, min_length=6, max_length=128)
    title: str | None = Field(default=None, max_length=80)
    discountLimit: Decimal | None = Field(default=None, ge=0, le=100)


class WorksAsRequest(BaseModel):
    """Salesperson (`cashier`) or Pharmacist (`pharmacist`): which Items the person sells at the till, and whether they
    take payment. Their Sales counter ticks start again from the new role's; other ticks, title and limit stay (a
    Pharmacist's limit becomes 0). A Branch Manager isn't switched, and nobody is made one this way."""

    # Checked by the server in plain words (services/rbac_service.py switch_works_as).
    roleId: str = Field(min_length=1, max_length=40)


class AbilityOut(BaseModel):
    key: str
    resource: str
    action: str
    label: str
    hint: str = ""


class AbilityGroupOut(BaseModel):
    key: str
    label: str
    abilities: list[AbilityOut]


class PresetOut(BaseModel):
    roleId: str
    label: str
    discountLimit: str
    abilities: list[str]


class AbilitiesOut(BaseModel):
    groups: list[AbilityGroupOut]
    presets: list[PresetOut]
