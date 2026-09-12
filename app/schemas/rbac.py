from pydantic import BaseModel

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


class UpdatePermissionsRequest(BaseModel):
    permissions: list[PermissionOut]
