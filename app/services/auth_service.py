from app.core.security import create_access_token, hash_password, verify_password
from app.models import LoginSession, User
from app.schemas.auth import LoginResponse, UserOut
from app.services.rbac_service import discount_limit_of, effective_permissions

# Where someone lands when their starting point's own screen isn't one they have.
_LANDINGS = (
    ("store.billing", "/store/billing"), ("branch-console.dashboard", "/branch-console/dashboard"),
    ("inventory.overview", "/inventory/overview"), ("inventory.transfers", "/inventory/transfers"),
    ("reports", "/reports"), ("branch-console.members", "/branch-console/members"), ("store.xz", "/store/xz"),
)
_PATH_RESOURCE = {path: resource for resource, path in _LANDINGS}


async def user_out(user: User, permissions) -> UserOut:
    readable = {p.resource for p in permissions if "R" in p.actions}
    landing = user.role.landing
    if _PATH_RESOURCE.get(landing, None) not in readable:
        landing = next((path for resource, path in _LANDINGS if resource in readable), landing)
    return UserOut(
        id=str(user.id), name=user.name, email=user.email, roleId=user.role_id, landing=landing, title=user.title,
        discountLimit=format(discount_limit_of(user).normalize(), "f"),
    )


async def authenticate(email: str, password: str) -> User | None:
    user = await User.get_or_none(email=email.strip().lower(), active=True).prefetch_related("role")
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


async def build_login_response(user: User, session: LoginSession) -> LoginResponse:
    """The token names the login it belongs to (services/login_session_service.py)."""
    await user.fetch_related("role")
    token = create_access_token(str(user.id), str(session.id), session.started_at)
    permissions = await effective_permissions(user)
    return LoginResponse(token=token, user=await user_out(user, permissions), permissions=permissions)


MIN_PASSWORD_LENGTH = 6


class AccountError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


def check_new_password(current_hash: str | None, new: str, confirm: str) -> None:
    if len(new) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"The new password needs at least {MIN_PASSWORD_LENGTH} characters.")
    if new != confirm:
        raise AccountError("The new password and its confirmation don't match.")
    if current_hash and verify_password(new, current_hash):
        raise AccountError("The new password is the same as your current one.")


async def change_own_password(user: User, current: str, new: str, confirm: str) -> None:
    if not verify_password(current, user.password_hash):
        raise AccountError("Your current password isn't right.")
    check_new_password(user.password_hash, new, confirm)
    user.password_hash = hash_password(new)
    await user.save()


async def change_own_name(user: User, name: str) -> None:
    """Names are what every sale, count and approval is traced to, so only a Branch Manager changes one,
    their own included."""
    if user.role_id != "branch-manager":
        raise AccountError("Ask your Branch Manager to change your name.", status=403)
    user.name = name.strip()
    await user.save()
