from datetime import datetime, timezone

from fastapi import HTTPException

from app.core import scheduler
from app.models import BranchIdentity
from app.schemas.registration import (
    BranchIdentityOut,
    RegistrationStatusOut,
    ResetIn,
    SyncCollectOut,
    SyncRunOut,
    SyncStatusOut,
    VerifyIn,
)
from app.services import downstream_service, registration_service, sync_service
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
        pullCursor=state.pull_cursor, lastPullAt=state.last_pull_at, lastPullError=state.last_pull_error,
    )


async def collect() -> SyncCollectOut:
    pulled = await downstream_service.pull_once(triggered_by="manual")
    pushed = await sync_service.push_events_once(triggered_by="manual")
    stock = await sync_service.push_stock_changes_once(triggered_by="manual")
    return SyncCollectOut(
        ok=pulled.ok and pushed.ok, applied=pulled.applied, failed=pulled.failed, sent=pushed.sent, stockItems=stock,
        pendingAfter=await sync_service.pending_count(), error=pulled.error or pushed.error,
        skippedReason=pulled.skipped_reason or pushed.skipped_reason,
    )


async def run_sync() -> SyncRunOut:
    pulled = await downstream_service.pull_once(triggered_by="manual")
    result = await sync_service.run_once(triggered_by="manual")
    return SyncRunOut(
        **result.as_dict(), pulledApplied=pulled.applied, pulledFailed=pulled.failed,
        pullError=pulled.error or pulled.skipped_reason if not pulled.ok else None,
    )
