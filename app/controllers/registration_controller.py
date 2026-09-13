from datetime import datetime, timezone

from fastapi import HTTPException

from app.core import scheduler
from app.models import BranchIdentity
from app.schemas.registration import (
    BranchIdentityOut,
    RegistrationStatusOut,
    ResetIn,
    SyncRunOut,
    SyncStatusOut,
    VerifyIn,
)
from app.services import registration_service, sync_service
from app.services.registration_service import RegistrationError


def _identity_out(identity: BranchIdentity) -> BranchIdentityOut:
    return BranchIdentityOut(
        branchId=identity.branch_id, code=identity.code, name=identity.name,
        address=identity.address, city=identity.city, phone=identity.phone,
        timezone=identity.timezone, cloudUrl=identity.cloud_url, verifiedAt=identity.verified_at,
    )


async def status() -> RegistrationStatusOut:
    identity = await registration_service.current()
    return RegistrationStatusOut(
        initialized=identity is not None,
        branch=_identity_out(identity) if identity else None,
        serverTime=datetime.now(timezone.utc),
    )


async def verify(payload: VerifyIn, claimed_from: str | None) -> RegistrationStatusOut:
    try:
        identity = await registration_service.verify(
            payload.cloudUrl, payload.code, payload.pairingKey, claimed_from,
        )
    except RegistrationError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return RegistrationStatusOut(
        initialized=True, branch=_identity_out(identity), serverTime=datetime.now(timezone.utc),
    )


async def reset(payload: ResetIn) -> None:
    try:
        await registration_service.reset(payload.confirmCode)
    except RegistrationError as exc:
        raise HTTPException(exc.status, exc.message) from exc


async def sync_status() -> SyncStatusOut:
    identity = await registration_service.current()
    state = await registration_service.sync_state()
    return SyncStatusOut(
        initialized=identity is not None,
        branch=_identity_out(identity) if identity else None,
        pendingEvents=await sync_service.pending_count(),
        sentEvents=state.events_sent,
        lastAttemptAt=state.last_attempt_at,
        lastSuccessAt=state.last_success_at,
        lastError=state.last_error,
        consecutiveFailures=state.consecutive_failures,
        running=state.running,
        schedulerActive=scheduler.is_running(),
        scheduleHint=scheduler.next_run_hint(),
        serverTime=datetime.now(timezone.utc),
    )


async def run_sync() -> SyncRunOut:
    result = await sync_service.run_once(triggered_by="manual")
    return SyncRunOut(**result.as_dict())
