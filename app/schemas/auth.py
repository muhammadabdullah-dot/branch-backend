from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    roleId: str
    landing: str
    title: str | None = None
    # The most bill discount this person may give, and approve for others.
    discountLimit: str = "0"


class PermissionOut(BaseModel):
    resource: str
    actions: list[str]


class LoginResponse(BaseModel):
    token: str
    user: UserOut
    permissions: list[PermissionOut]


class MeResponse(BaseModel):
    user: UserOut
    permissions: list[PermissionOut]


class ChangePasswordRequest(BaseModel):
    """Changing your own password: the current one proves it's you, the new one twice guards against a typo."""

    currentPassword: str = Field(min_length=1, max_length=128)
    newPassword: str = Field(min_length=1, max_length=128)
    confirmPassword: str = Field(min_length=1, max_length=128)


class MyNameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class EndLoginRequest(BaseModel):
    """Ending someone's login: why, in a few words ("Shift over", "Forgot to sign out")."""

    reason: str = Field(min_length=1, max_length=200)


class DeviceNameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class SignInRulesRequest(BaseModel):
    """How one person signs in. `counterStaff` null follows how they started (on for a Salesperson who rings up sales
    or a Pharmacist who makes slips). `deviceIds` are the registered devices they may sign in on; empty means any computer here."""

    counterStaff: bool | None = None
    deviceIds: list[str] = Field(default_factory=list, max_length=50)
