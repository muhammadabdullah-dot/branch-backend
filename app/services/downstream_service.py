"""Collecting what head office sends this branch.

Head office keeps a numbered queue per branch: staff accounts it created or changed, role access it set,
transfers on their way here or updates on ones this branch sent, and the company's suppliers. This module
asks for everything after the last number it applied, applies each message, moves its cursor on, and tells
head office how each one went.

Every message carries the whole thing as it now stands, so applying one twice changes nothing — a pull
that dies halfway is simply repeated. A message this branch can't apply (an unknown role, an email
already taken) is reported back with the reason and skipped, so one bad message never holds up the
ones behind it; head office shows the reason against the change.
"""
import asyncio
from datetime import datetime, timezone

import httpx

from app.core import logs
from app.core.resources import RESOURCES, excluded_resources_for_role, resources_for_role
from app.models import IDENTITY_PK, BranchIdentity, Role, RoleDefaultPermission, SyncState
from app.services import members_service, registration_service, staff_sync_service, transfers_service

PAGE_SIZE = 200
MAX_PAGES_PER_RUN = 25
TIMEOUT_SECONDS = 30.0

_lock = asyncio.Lock()
_manifest_sent = False


class PullResult:
    def __init__(self) -> None:
        self.ok = False
        self.applied = 0
        self.failed = 0
        self.cursor = 0
        self.latest = 0
        self.error: str | None = None
        self.skipped_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "applied": self.applied, "failed": self.failed, "cursor": self.cursor,
            "latest": self.latest, "error": self.error, "skippedReason": self.skipped_reason,
        }


def _headers(identity: BranchIdentity) -> dict[str, str]:
    return {"X-Branch-Code": identity.code, "X-Branch-Secret": identity.sync_secret}


async def _manifest() -> dict:
    roles = []
    for role in await Role.all().order_by("id"):
        if role.managed_by_head_office:
            resources = set(await RoleDefaultPermission.filter(role=role).values_list("resource", flat=True))
        else:
            resources = resources_for_role(role.id)
        roles.append({
            "id": role.id, "name": role.name, "resources": sorted(resources),
            "exclude": sorted(excluded_resources_for_role(role.id)),
        })
    return {"resources": list(RESOURCES), "roles": roles}


async def apply(kind: str, payload: dict) -> str:
    """Apply one message. Raises with a plain reason when it can't be applied."""
    if kind == "staff.upsert":
        return await staff_sync_service.apply_upsert(payload.get("user") or {})
    if kind == "staff.remove":
        return await staff_sync_service.apply_remove(payload)
    if kind == "role.template":
        return await staff_sync_service.apply_role_template(payload)
    if kind == "transfer.inbound":
        return await transfers_service.apply_inbound(payload.get("transfer") or {})
    if kind == "transfer.outbound":
        return await transfers_service.apply_outbound(payload.get("transfer") or {})
    if kind == "member.upsert":
        return await members_service.apply_member(payload.get("member") or {})
    if kind == "loyalty.entry":
        if payload.get("member"):
            from app.models import Member

            code = (payload["member"].get("code") or "").upper()
            if code and not await Member.exists(code=code):
                await members_service.apply_member(payload["member"])
        return await members_service.apply_entry(payload.get("entry") or {})
    if kind == "loyalty.settings":
        return await members_service.apply_settings(payload.get("settings") or {})
    if kind == "supplier.upsert":
        from app.services import supplier_sync_service

        return await supplier_sync_service.apply_upsert(payload)
    if kind == "supplier.list":
        from app.services import supplier_sync_service

        return await supplier_sync_service.apply_list(payload)
    # A newer head office can send kinds this branch software doesn't know yet; say so rather than fail.
    return "unknown-kind"


def _reason(exc: Exception) -> str:
    return getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"


async def _send_acks(client: httpx.AsyncClient, identity: BranchIdentity, state: SyncState) -> None:
    if not state.pending_acks:
        return
    response = await client.post(f"{identity.cloud_url}/sync/ack", json={"results": state.pending_acks}, headers=_headers(identity))
    if response.status_code < 400:
        state.pending_acks = []
        await state.save(update_fields=["pending_acks"])


async def pull_once(*, triggered_by: str = "scheduler") -> PullResult:
    global _manifest_sent
    result = PullResult()
    identity = await registration_service.current()
    if identity is None:
        result.skipped_reason = "This branch server isn't verified yet, so there is no head office to collect from."
        return result
    if _lock.locked():
        result.skipped_reason = "Already collecting from head office."
        return result

    async with _lock:
        state, _ = await SyncState.get_or_create(id=IDENTITY_PK)
        result.cursor = state.pull_cursor
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                if not _manifest_sent:
                    response = await client.post(f"{identity.cloud_url}/sync/manifest", json=await _manifest(), headers=_headers(identity))
                    _manifest_sent = response.status_code < 400
                    if _manifest_sent:
                        # The same list in the branch's own words, so head office's Branch Staff screen reads like the
                        # branch's access screen. A head office too old to take it just says no; that's not a failure.
                        from app.services.rbac_service import abilities_catalog

                        await client.post(f"{identity.cloud_url}/rbac/branch-abilities", json=abilities_catalog(), headers=_headers(identity))
                await _send_acks(client, identity, state)

                for _ in range(MAX_PAGES_PER_RUN):
                    response = await client.get(
                        f"{identity.cloud_url}/sync/pull", params={"after": state.pull_cursor, "limit": PAGE_SIZE},
                        headers=_headers(identity),
                    )
                    if response.status_code == 401:
                        result.error = "Head office no longer recognises this branch's credentials."
                        break
                    if response.status_code >= 400:
                        result.error = f"Head office refused the request for updates ({response.status_code})."
                        break
                    data = response.json()
                    result.latest = int(data.get("latestSeq") or 0)
                    await transfers_service.replace_known_branches(data.get("branches") or [])

                    messages = data.get("messages") or []
                    for message in messages:
                        try:
                            seq = int(message["seq"])
                        except (KeyError, TypeError, ValueError):
                            # No usable number means nothing to ack and nothing to move the cursor to.
                            # Note it and take the next one rather than abandoning the page.
                            result.failed += 1
                            logs.log.warning("head office sent a message with no usable number; skipped: %r", message)
                            continue
                        if seq <= state.pull_cursor:
                            continue
                        try:
                            await apply(message.get("kind"), message.get("payload") or {})
                            outcome = {"seq": seq, "ok": True}
                            result.applied += 1
                        except Exception as exc:  # noqa: BLE001 — one bad message must not stop the rest
                            outcome = {"seq": seq, "ok": False, "error": _reason(exc)[:900]}
                            result.failed += 1
                            logs.log.warning("head office message %s (%s) not applied: %s", seq, message.get("kind"), outcome["error"])
                        # Cursor and the result to report move together, so a crash here repeats at most this one message.
                        state.pull_cursor = seq
                        state.pending_acks = [*state.pending_acks, outcome]
                        await state.save(update_fields=["pull_cursor", "pending_acks"])
                    await _send_acks(client, identity, state)
                    if len(messages) < PAGE_SIZE or state.pull_cursor >= result.latest:
                        break
        except httpx.HTTPError as exc:
            result.error = f"No connection to head office ({type(exc).__name__}). Updates will be collected next time."
        except Exception as exc:  # noqa: BLE001 — nothing a pull runs into may leave this function
            # Nothing a pull runs into may leave this function by raising. This call sits first in the
            # quick tick and behind POST /sync/collect: an escaping error would skip the branch's own
            # push for that tick and turn the button into a 500, while the reason went nowhere useful.
            result.error = f"Couldn't collect updates from head office ({type(exc).__name__})."
            logs.log.error("sync: collecting from head office failed", exc_info=exc)

        state.last_pull_at = datetime.now(timezone.utc)
        state.last_pull_error = result.error
        await state.save(update_fields=["last_pull_at", "last_pull_error"])
        result.cursor = state.pull_cursor
        result.ok = result.error is None
    if result.applied or result.failed or result.error:
        print(
            f"  pull [{triggered_by}]: {result.applied} applied, {result.failed} not applied, "
            f"at {result.cursor}/{result.latest}" + (f" | {result.error}" if result.error else ""),
            flush=True,
        )
    return result
