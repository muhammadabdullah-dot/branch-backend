"""The branch half of the handshake: spend the key main office gave us, and remember who we are.

**This module writes exactly one row and touches nothing else.** That is a deliberate, load-bearing
constraint, not a description of what the code happens to do today. A branch being initialized is
almost never an empty branch — it is a shop that has been trading on this database, with users,
stock, sales history, held bills. Verification is the branch learning its name. It is not a reset,
not a re-seed, and not an import. Any future change here that deletes or overwrites anything
outside `branch_identity` is a bug, however reasonable it looks in isolation.

The other rule is that identity is written once. `verify()` refuses outright if an identity already
exists rather than updating it, because "update the branch code" and "silently re-point this shop's
sales at a different branch" are the same operation.
"""
from datetime import datetime, timezone

import httpx
from tortoise.exceptions import IntegrityError

from app.models import IDENTITY_PK, BranchIdentity, SyncState

# Verification is a person standing at a terminal waiting for it. Long enough for a slow DSL line
# in a Pakistani branch, short enough that "it's hung" is never the right conclusion.
VERIFY_TIMEOUT_SECONDS = 20.0


class RegistrationError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


def normalize_cloud_url(raw: str) -> str:
    """`localhost:4175` is what someone types; `http://localhost:4175` is what httpx needs. Being
    strict here would mean rejecting a correct answer on a formatting technicality."""
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise RegistrationError("Head office address is required.", status=422)
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


async def current() -> BranchIdentity | None:
    return await BranchIdentity.get_or_none(id=IDENTITY_PK)


async def sync_state() -> SyncState:
    state, _ = await SyncState.get_or_create(id=IDENTITY_PK)
    return state


async def verify(cloud_url: str, code: str, pairing_key: str, claimed_from: str | None) -> BranchIdentity:
    """Present our key to the Cloud and, only if it says yes, write who we are.

    Order matters and is not negotiable: the network call happens first, and the local write only
    happens after the Cloud has confirmed. A branch that wrote its identity optimistically and then
    failed to reach the Cloud would sit there looking verified while holding a secret that no Cloud
    has ever heard of — and because identity is write-once, it would be stuck that way.
    """
    existing = await current()
    if existing is not None:
        raise RegistrationError(
            f"This branch server is already set up as {existing.code} ({existing.name}). "
            "Branch details can't be changed here; main office must revoke its pairing first.",
            status=409,
        )

    base = normalize_cloud_url(cloud_url)
    wanted = (code or "").strip().upper()
    if not wanted:
        raise RegistrationError("Branch code is required.", status=422)
    if not (pairing_key or "").strip():
        raise RegistrationError("Verification key is required.", status=422)

    payload = {
        "code": wanted,
        "pairingKey": pairing_key.strip(),
        "claimedFrom": claimed_from,
    }

    try:
        async with httpx.AsyncClient(timeout=VERIFY_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{base}/registration/claim", json=payload)
    except httpx.TimeoutException as exc:
        raise RegistrationError(
            f"Head office at {base} didn't answer in time. Check the internet connection and the "
            "address, then try again.",
            status=504,
        ) from exc
    except httpx.HTTPError as exc:
        raise RegistrationError(
            f"Couldn't reach head office at {base}. Check the internet connection and the address.",
            status=502,
        ) from exc

    if response.status_code >= 400:
        # Pass the Cloud's own wording through — it is written for exactly this reader, and
        # replacing it with something generic would hide "already verified elsewhere", which is
        # the one failure whose cause is not guessable from the branch side.
        raise RegistrationError(_cloud_message(response), status=response.status_code)

    data = response.json()
    secret = data.get("syncSecret")
    if not secret:
        raise RegistrationError(
            "Head office accepted the key but didn't return sync credentials. Nothing has been "
            "saved. Tell main office and try again.",
            status=502,
        )

    try:
        identity = await BranchIdentity.create(
            id=IDENTITY_PK,
            branch_id=str(data.get("branchId") or ""),
            code=data.get("code") or wanted,
            name=data.get("name") or wanted,
            address=data.get("address"),
            city=data.get("city"),
            phone=data.get("phone"),
            timezone=data.get("timezone") or "Asia/Karachi",
            cloud_url=base,
            sync_secret=secret,
            verified_at=_parse_dt(data.get("verifiedAt")) or datetime.now(timezone.utc),
        )
    except IntegrityError as exc:
        # Two verification attempts raced. The first one won; this one must not overwrite it.
        raise RegistrationError(
            "This branch server was set up by another request a moment ago. Reload the page.",
            status=409,
        ) from exc

    # Bring the sync bookkeeping row into existence now, so the status screen has something to
    # read before the first tick ever fires.
    await sync_state()
    return identity


async def reset(confirm_code: str) -> None:
    """Forget this branch's identity so it can be verified again.

    Needed for a genuine case — a branch re-paired after a server rebuild, or a test run — but
    deliberately awkward: the caller must type the branch's own code back. This deletes the
    identity row and the sync bookkeeping row. **It does not touch a single business record.**
    Sales, stock, users, history: all untouched. What is lost is the credential, which the Cloud
    must reissue anyway.
    """
    identity = await current()
    if identity is None:
        raise RegistrationError("This branch server isn't set up yet.", status=404)
    if (confirm_code or "").strip().upper() != identity.code.upper():
        raise RegistrationError(
            f"Type the branch code ({identity.code}) to confirm.", status=422,
        )
    await identity.delete()
    state = await SyncState.get_or_none(id=IDENTITY_PK)
    if state is not None:
        await state.delete()


def _cloud_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"Head office refused the request ({response.status_code})."
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and detail.strip():
        return detail
    message = body.get("message") if isinstance(body, dict) else None
    if isinstance(message, str) and message.strip():
        return message
    return f"Head office refused the request ({response.status_code})."


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
