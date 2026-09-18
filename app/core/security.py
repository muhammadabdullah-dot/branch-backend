"""Password hashing and JWT issuance/verification. Each token names its login (see models/login_session.py)."""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=12)

# A manager's approval of an above-authority discount. Long enough to survive a refresh of the till
# or a customer fetching one more thing; short enough that an approval nobody used can't be kept
# for later.
DISCOUNT_APPROVAL_TTL = timedelta(minutes=15)
DISCOUNT_APPROVAL_AUDIENCE = "dmarina:discount-approval"


def _discount_approval_key() -> bytes:
    """Its own key, derived from the JWT secret, so an approval can never pass for a sign-in token
    (it would name the manager, and hand whoever held it their whole session) or the other way round."""
    return hmac.new(settings.jwt_secret.encode(), b"dmarina/discount-approval", hashlib.sha256).digest()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user_id: str, session_id: str | None = None, issued_at: datetime | None = None) -> str:
    """A sign-in token. `sid` names the login it belongs to (models/login_session.py): a token is only good
    while that login is, so a login ended by sign-out or by a manager stops working at once."""
    now = issued_at or datetime.now(timezone.utc)
    payload = {"sub": user_id, "iat": now, "exp": now + TOKEN_TTL}
    if session_id:
        payload["sid"] = session_id
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])


def create_discount_approval_token(claims: dict, now: datetime) -> str:
    payload = {**claims, "aud": DISCOUNT_APPROVAL_AUDIENCE, "iat": now, "exp": now + DISCOUNT_APPROVAL_TTL}
    return jwt.encode(payload, _discount_approval_key(), algorithm=ALGORITHM)


def decode_discount_approval_token(token: str) -> dict:
    """Raises jwt.ExpiredSignatureError for a lapsed approval, another jwt.PyJWTError for anything forged or mangled."""
    return jwt.decode(
        token, _discount_approval_key(), algorithms=[ALGORITHM], audience=DISCOUNT_APPROVAL_AUDIENCE,
        options={"require": ["exp", "iat", "aud"]},
    )
