"""Pushing this branch's outbox to head office.

The contract with the rest of the branch server is narrow: business code writes `OutboxEvent` rows
and never thinks about the network; this module reads them and never thinks about business rules.
Nothing here interprets a sale or a stock movement — it moves envelopes.

Three properties matter more than throughput, because branch links in Pakistan drop constantly:

**Nothing is deleted.** A pushed event is marked `sent`, not removed. The ledger of what this
branch has told head office stays in the branch, which is what makes "did that sale reach cloud?"
answerable six weeks later instead of a shrug.

**Only what the Cloud acknowledges is marked sent.** The Cloud returns the ids it actually stored.
An event that fails to land is simply absent from that list and stays pending, so it goes again on
the next tick. Losing an event requires the Cloud to lie about having it.

**Being offline is not an error.** A shop with no internet for a day is a Tuesday, not an incident.
A failed tick records why, leaves everything pending, and the next tick picks up where it left off.
"""
from datetime import datetime, timedelta, timezone

import httpx
from tortoise.expressions import Q

from app.models import IDENTITY_PK, BranchIdentity, OutboxEvent, SyncState
from app.services import registration_service, snapshot_service

# One push, not one giant one. A branch that has been offline for a week has thousands of pending
# events; sending them as a single body means one dropped connection wastes the whole thing, and a
# body large enough to time out never succeeds no matter how many times it is retried.
BATCH_SIZE = 250
# How many batches one run will send before stopping. A backlog does drain — it just drains over
# several ticks rather than holding the connection open for an unbounded stretch.
MAX_BATCHES_PER_RUN = 20
PUSH_TIMEOUT_SECONDS = 30.0
# The snapshot folds run over every sale line and stock movement the branch holds, then ship tens
# of thousands of stock rows. Generous, because the alternative to waiting is a dashboard that is
# two hours stale for no reason.
SNAPSHOT_TIMEOUT_SECONDS = 120.0
# How long a claimed-but-silent run is believed before another run takes over. Comfortably longer
# than the slowest legitimate sync (a full stock push runs well under two minutes), so a healthy
# slow run is never interrupted, and short enough that a branch recovers from a hard kill by
# itself rather than needing somebody to notice.
STALE_LOCK_AFTER = timedelta(minutes=15)


class SyncResult:
    """What one run did, in the terms an operator asks about: did it work, how much moved, why not."""

    def __init__(self) -> None:
        self.ok: bool = False
        self.sent: int = 0
        self.duplicates: int = 0
        self.pending_before: int = 0
        self.pending_after: int = 0
        self.batches: int = 0
        self.error: str | None = None
        self.skipped_reason: str | None = None
        # The reporting half — see `_push_snapshot`. Separate counters because "the event stream is
        # up to date" and "head office's figures are up to date" are different questions, and an
        # operator who is told only one of them will assume the other.
        self.snapshot_ok: bool = False
        self.trading_days: int = 0
        self.product_days: int = 0
        self.stock_rows: int = 0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "sent": self.sent, "duplicates": self.duplicates,
            "pendingBefore": self.pending_before, "pendingAfter": self.pending_after,
            "batches": self.batches, "error": self.error, "skippedReason": self.skipped_reason,
            "snapshotOk": self.snapshot_ok, "tradingDays": self.trading_days,
            "productDays": self.product_days, "stockRows": self.stock_rows,
        }


def _headers(identity: BranchIdentity) -> dict[str, str]:
    return {"X-Branch-Code": identity.code, "X-Branch-Secret": identity.sync_secret}


def _serialize(event: OutboxEvent) -> dict:
    return {
        "id": str(event.id),
        "aggregateType": event.aggregate_type,
        "aggregateId": event.aggregate_id,
        "payload": event.payload,
        "originUserId": event.origin_user_id,
        "originDeviceId": event.origin_device_id,
        "createdAt": event.created_at.isoformat() if event.created_at else None,
    }


async def pending_count() -> int:
    return await OutboxEvent.filter(Q(status="pending") | Q(status="failed")).count()


async def ping(identity: BranchIdentity) -> tuple[bool, str | None]:
    """Is head office reachable, and does it still know us? Cheap enough to call from a status
    screen, and it is also how a tick decides whether to bother building a batch."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{identity.cloud_url}/sync/hello", headers=_headers(identity),
            )
    except httpx.HTTPError as exc:
        return False, f"Head office unreachable: {type(exc).__name__}"
    if response.status_code == 401:
        return False, "Head office no longer recognises this branch's credentials."
    if response.status_code >= 400:
        return False, f"Head office returned {response.status_code}."
    return True, None


async def run_once(*, triggered_by: str = "scheduler") -> SyncResult:
    """One sync run. Safe to call at any time, including while a scheduled run is in flight — the
    second caller is told a run is already going rather than sending the same events twice."""
    result = SyncResult()
    identity = await registration_service.current()
    if identity is None:
        result.skipped_reason = "This branch server isn't verified yet, so there is nowhere to sync to."
        return result

    await SyncState.get_or_create(id=IDENTITY_PK)
    now = datetime.now(timezone.utc)

    # Claim the run with a single conditional UPDATE rather than read-then-write. Reading
    # `running`, deciding, and then saving leaves an await between the decision and the write, and
    # two presses a few milliseconds apart can both pass the check — which is exactly the case
    # somebody double-clicking a button produces. `update()` returns the number of rows it changed,
    # so only one caller can win.
    claimed = await SyncState.filter(id=IDENTITY_PK, running=False).update(
        running=True, last_attempt_at=now,
    )
    if not claimed:
        # Someone holds the lock. Either a run really is in flight, or a previous run was killed
        # mid-flight and never released it — a process killed with SIGKILL never reaches a `finally`.
        # Without this, one hard kill would leave the branch permanently convinced a sync is running
        # and it would never sync again, silently, which is the worst failure this system has.
        stale_before = now - STALE_LOCK_AFTER
        recovered = await SyncState.filter(
            id=IDENTITY_PK, running=True, last_attempt_at__lt=stale_before,
        ).update(last_attempt_at=now)
        if not recovered:
            result.skipped_reason = "A sync is already running."
            return result
        print(
            f"  sync [{triggered_by}]: previous run never finished "
            f"(no progress for {STALE_LOCK_AFTER}); taking over.",
            flush=True,
        )

    state = await SyncState.get(id=IDENTITY_PK)

    try:
        result.pending_before = await pending_count()
        await _drain(identity, result)
        # The snapshot goes even when the event drain hit trouble, as long as the link is up at
        # all: the two carry different things, and refusing to update head office's figures because
        # one event would not stick would be punishing the wrong thing.
        await _push_snapshot(identity, result)
        result.ok = result.error is None
    finally:
        # `running` must come down even if something above threw, or one crash leaves this branch
        # permanently convinced a sync is in progress and it never syncs again.
        state.running = False
        if result.ok:
            state.last_success_at = datetime.now(timezone.utc)
            state.last_error = None
            state.consecutive_failures = 0
        elif result.error:
            state.last_error = result.error
            state.consecutive_failures += 1
        # Net new, not raw acknowledgements. A re-sent event that head office already had is a
        # success, but counting it again would let this figure drift above the number of things
        # that ever happened in the branch — and the person reading it compares it to their day.
        state.events_sent += max(result.sent - result.duplicates, 0)
        await state.save()

    result.pending_after = await pending_count()
    if result.ok:
        print(
            f"  sync [{triggered_by}]: {result.sent} sent, {result.duplicates} already there, "
            f"{result.pending_after} still pending | figures: {result.trading_days} days, "
            f"{result.product_days} product-days, {result.stock_rows} stock rows",
            flush=True,
        )
    elif result.error:
        print(f"  sync [{triggered_by}]: {result.error}", flush=True)
    return result


async def _drain(identity: BranchIdentity, result: SyncResult) -> None:
    async with httpx.AsyncClient(timeout=PUSH_TIMEOUT_SECONDS) as client:
        for _ in range(MAX_BATCHES_PER_RUN):
            events = (
                await OutboxEvent.filter(Q(status="pending") | Q(status="failed"))
                .order_by("created_at")
                .limit(BATCH_SIZE)
            )
            if not events:
                return

            body = {"events": [_serialize(e) for e in events]}
            try:
                response = await client.post(
                    f"{identity.cloud_url}/sync/push", json=body, headers=_headers(identity),
                )
            except httpx.TimeoutException:
                result.error = "Head office didn't answer in time. Everything stays queued and will go again next time."
                await _mark_failed(events, result.error)
                return
            except httpx.HTTPError:
                result.error = "No connection to head office. Everything stays queued and will go again next time."
                await _mark_failed(events, result.error)
                return

            if response.status_code == 401:
                result.error = (
                    "Head office no longer recognises this branch's credentials. Its pairing may "
                    "have been revoked — nothing has been sent."
                )
                await _mark_failed(events, result.error)
                return
            if response.status_code >= 400:
                result.error = f"Head office refused the batch ({response.status_code}). Everything stays queued."
                await _mark_failed(events, result.error)
                return

            data = response.json()
            acknowledged = set(data.get("acknowledgedIds") or [])
            result.duplicates += int(data.get("duplicates") or 0)
            result.batches += 1

            confirmed = [e for e in events if str(e.id) in acknowledged]
            unconfirmed = [e for e in events if str(e.id) not in acknowledged]

            now = datetime.now(timezone.utc)
            for event in confirmed:
                event.status = "sent"
                event.last_attempt_at = now
                event.attempt_count += 1
                event.last_error = None
                await event.save(update_fields=["status", "last_attempt_at", "attempt_count", "last_error"])
            result.sent += len(confirmed)

            if unconfirmed:
                # Head office took the call but not these rows. Leaving them pending is right, but
                # sending the same batch again immediately would spin — stop and let the next tick
                # try, with the reason recorded.
                await _mark_failed(unconfirmed, "Head office did not confirm these events.")
                result.error = (
                    f"{len(unconfirmed)} event(s) weren't confirmed by head office and stay queued."
                )
                return


async def _push_snapshot(identity: BranchIdentity, result: SyncResult) -> None:
    """Send head office this branch's own picture of itself: trading days, product-days, cashiers,
    hours, till closes, tenders, overrides, returns, credit customers, alerts — then the item-level
    stock list in chunks.

    This is the half that actually feeds the Executive dashboard. The event stream says *that*
    things happened; this says *what the numbers are*. Failing here is recorded but never raises —
    a branch that can't report its figures this hour still queues its events for the next one.
    """
    try:
        aggregates = await snapshot_service.build_aggregates()
        rows = await snapshot_service.stock_rows()
    except Exception as exc:  # noqa: BLE001 — a bad fold must not take the whole sync down
        result.error = result.error or f"Couldn't build this branch's figures ({type(exc).__name__})."
        return

    snapshot_id = snapshot_service.new_snapshot_id()
    try:
        async with httpx.AsyncClient(timeout=SNAPSHOT_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{identity.cloud_url}/sync/snapshot", json=aggregates, headers=_headers(identity),
            )
            if response.status_code >= 400:
                result.error = result.error or f"Head office refused this branch's figures ({response.status_code})."
                return
            summary = response.json()
            result.trading_days = int(summary.get("tradingDays") or 0)
            result.product_days = int(summary.get("productDays") or 0)

            # Chunked, and only promoted at the end: a link that dies halfway must leave head
            # office showing the last complete stock picture, not half a shop.
            sent_rows = 0
            for chunk in snapshot_service.chunked(rows):
                chunk_response = await client.post(
                    f"{identity.cloud_url}/sync/stock",
                    json={"snapshotId": snapshot_id, "rows": chunk}, headers=_headers(identity),
                )
                if chunk_response.status_code >= 400:
                    result.error = result.error or (
                        f"Head office refused part of the stock list ({chunk_response.status_code}). "
                        "Its previous stock picture is unchanged."
                    )
                    return
                sent_rows += len(chunk)

            done = await client.post(
                f"{identity.cloud_url}/sync/stock/complete",
                json={"snapshotId": snapshot_id}, headers=_headers(identity),
            )
            if done.status_code >= 400:
                result.error = result.error or f"Head office couldn't finalise the stock list ({done.status_code})."
                return
            result.stock_rows = int(done.json().get("stockRows") or sent_rows)
            result.snapshot_ok = True
    except httpx.HTTPError:
        result.error = result.error or (
            "No connection to head office while sending this branch's figures. "
            "Its previous figures are unchanged and this will go again next time."
        )


async def _mark_failed(events: list[OutboxEvent], reason: str) -> None:
    """Record the attempt without losing the event. `failed` is picked up again by the next run —
    it means "tried and didn't land", not "give up"."""
    now = datetime.now(timezone.utc)
    for event in events:
        event.status = "failed"
        event.attempt_count += 1
        event.last_attempt_at = now
        event.last_error = reason[:500]
        await event.save(update_fields=["status", "attempt_count", "last_attempt_at", "last_error"])
