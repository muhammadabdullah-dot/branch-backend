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
the next tick. Losing an event requires the Cloud to lie about having it. The one exception is an
event the Cloud refuses over and over: the queue goes oldest first, so that one event would sit in
front of everything the branch has to say for the rest of time. After a few refusals it is set
aside — still here, with the reason written on it, but out of the way (`MAX_REFUSALS_PER_EVENT`).

**Being offline is not an error.** A shop with no internet for a day is a Tuesday, not an incident.
A failed tick records why, leaves everything pending, and the next tick picks up where it left off.
"""
from datetime import datetime, timedelta, timezone

import httpx
from tortoise.expressions import Q

from app.core import logs
from app.models import IDENTITY_PK, BranchIdentity, OutboxEvent, SyncState
from app.services import registration_service, snapshot_service

# One push, not one giant one. A branch that has been offline for a week has thousands of pending
# events; sending them as a single body means one dropped connection wastes the whole thing, and a
# body large enough to time out never succeeds no matter how many times it is retried.
BATCH_SIZE = 250

# How many times head office may refuse the same event before the branch stops offering it. This
# counts refusals only — a batch that never reached head office at all is not one of them, because
# being offline must never cost an event. Five is chosen against the quick push's rhythm: roughly
# ten minutes of being refused, which no transient at head office lasts through, and short enough
# that a branch is not stuck behind one bad event for a shift.
MAX_REFUSALS_PER_EVENT = 5

# The last sync problem written to the log: a branch that is offline for a day fails the same way every few minutes,
# and the log needs the first time and the recovery, not a line per attempt.
_last_sync_error: str | None = None
_last_sync_error_logged_at: datetime | None = None
_attempts_since_logged = 0
# How long the same problem stays quiet in the file before it says so again. A branch offline for a
# week fails every couple of minutes: one line per attempt is five thousand lines nobody reads, and
# one line for the whole week reads as a blip that fixed itself. Half-hourly, with the number of
# attempts behind it, is what an operator opening server.log a week later actually needs.
REPEAT_LOG_AFTER = timedelta(minutes=30)


def _note_sync_error(triggered_by: str, error: str) -> None:
    global _last_sync_error, _last_sync_error_logged_at, _attempts_since_logged
    now = datetime.now(timezone.utc)
    fresh = error != _last_sync_error or _last_sync_error_logged_at is None
    if fresh:
        logs.log.warning("sync [%s]: %s", triggered_by, error)
        _attempts_since_logged = 0
        _last_sync_error_logged_at = now
    elif now - _last_sync_error_logged_at >= REPEAT_LOG_AFTER:
        logs.log.warning(
            "sync [%s]: still failing (%s more attempt(s) since the last entry): %s",
            triggered_by, _attempts_since_logged, error,
        )
        _attempts_since_logged = 0
        _last_sync_error_logged_at = now
    else:
        # Held back from the file, not dropped — the console still gets its line per attempt, and the
        # count above says how many of these there were.
        _attempts_since_logged += 1
        print(f"  sync [{triggered_by}]: {error}", flush=True)
    _last_sync_error = error


def _note_sync_working() -> None:
    global _last_sync_error, _last_sync_error_logged_at, _attempts_since_logged
    if _last_sync_error:
        logs.log.info("sync: working again (last problem: %s)", _last_sync_error)
    _last_sync_error = None
    _last_sync_error_logged_at = None
    _attempts_since_logged = 0
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
        # Events head office kept refusing, now out of the queue. Separate from `pending`, because
        # "still trying" and "given up on until somebody looks" are different answers.
        self.set_aside: int = 0
        self.error: str | None = None
        self.skipped_reason: str | None = None
        # The reporting half — see `_push_snapshot`. Separate counters because "the event stream is
        # up to date" and "head office's figures are up to date" are different questions, and an
        # operator who is told only one of them will assume the other.
        self.snapshot_ok: bool = False
        self.trading_days: int = 0
        self.product_days: int = 0
        self.stock_rows: int = 0
        # Set when the full stock list landed: what it covered, so the quick loop picks up from there.
        self.stock_mark: int | None = None
        self.stock_mark_at: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "sent": self.sent, "duplicates": self.duplicates,
            "pendingBefore": self.pending_before, "pendingAfter": self.pending_after,
            "batches": self.batches, "setAside": self.set_aside,
            "error": self.error, "skippedReason": self.skipped_reason,
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


async def _claim(now: datetime, triggered_by: str) -> bool:
    claimed = await SyncState.filter(id=IDENTITY_PK, running=False).update(
        running=True, last_attempt_at=now,
    )
    if claimed:
        return True
    # Someone holds the lock. Either a run really is in flight, or a previous run was killed
    # mid-flight and never released it — a process killed with SIGKILL never reaches a `finally`.
    # Without this, one hard kill would leave the branch permanently convinced a sync is running
    # and it would never sync again, silently, which is the worst failure this system has.
    stale_before = now - STALE_LOCK_AFTER
    recovered = await SyncState.filter(
        id=IDENTITY_PK, running=True, last_attempt_at__lt=stale_before,
    ).update(last_attempt_at=now)
    if recovered:
        print(
            f"  sync [{triggered_by}]: previous run never finished "
            f"(no progress for {STALE_LOCK_AFTER}); taking over.",
            flush=True,
        )
    return bool(recovered)


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
    if not await _claim(now, triggered_by):
        result.skipped_reason = "A sync is already running."
        return result

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
        if result.stock_mark is not None:
            state.stock_mark, state.stock_mark_at = result.stock_mark, result.stock_mark_at
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
        _note_sync_working()
    elif result.error:
        _note_sync_error(triggered_by, result.error)
    return result


async def push_events_once(*, triggered_by: str = "quick") -> SyncResult:
    """Send pending events only — no figures. Shares the full run's claim, so the two never send the
    same events at once; skipped quietly when there's nothing pending or a run is going."""
    result = SyncResult()
    identity = await registration_service.current()
    if identity is None:
        result.skipped_reason = "This branch server isn't verified yet."
        return result
    result.pending_before = await pending_count()
    if not result.pending_before:
        result.ok = True
        return result
    await SyncState.get_or_create(id=IDENTITY_PK)
    now = datetime.now(timezone.utc)
    if not await _claim(now, triggered_by):
        result.skipped_reason = "A sync is already running."
        return result
    try:
        await _drain(identity, result)
        result.ok = result.error is None
    finally:
        state = await SyncState.get(id=IDENTITY_PK)
        state.running = False
        state.events_sent += max(result.sent - result.duplicates, 0)
        fields = ["running", "events_sent"]
        if result.error:
            # This is the loop most problems actually show up on — it runs every couple of minutes,
            # the full run only every two hours — so its reason has to reach the status screen too.
            # Clearing it stays with the full run, which is the one that knows both halves are well.
            state.last_error = result.error
            fields.append("last_error")
        await state.save(update_fields=fields)
    result.pending_after = await pending_count()
    if result.error:
        _note_sync_error(triggered_by, result.error)
    elif result.sent:
        print(f"  sync [{triggered_by}]: {result.sent} sent, {result.pending_after} still pending", flush=True)
    return result


# More changed items than this at once (an import, a big receipt) wait for the next full stock push.
STOCK_CHANGES_MAX = 5000


async def push_stock_changes_once(*, triggered_by: str = "quick") -> int:
    """Bring head office's item-level stock up to date for just the items that moved since it was last
    told — a sale, a receipt, a transfer — without resending the whole list. Returns how many items went.

    Nothing happens until one full stock push has landed (head office needs a list to update), or while
    another run holds the claim."""
    identity = await registration_service.current()
    if identity is None:
        return 0
    state, _ = await SyncState.get_or_create(id=IDENTITY_PK)
    if state.stock_mark_at is None:
        return 0
    started = datetime.now(timezone.utc)
    ids, mark = await snapshot_service.changed_product_ids(state.stock_mark, state.stock_mark_at - timedelta(minutes=5))
    if not ids or len(ids) > STOCK_CHANGES_MAX:
        return 0
    if not await _claim(started, triggered_by):
        return 0
    sent = 0
    new_mark: tuple[int, datetime] | None = None
    # Why per-item stock stopped flowing, if it did. Silence here used to be indistinguishable from
    # "nothing moved", so head office could sit on hours-old shelf quantities with nothing anywhere
    # saying so — the one number branch staff ring head office about.
    problem: str | None = None
    try:
        rows = await snapshot_service.stock_rows(ids)
        async with httpx.AsyncClient(timeout=SNAPSHOT_TIMEOUT_SECONDS) as client:
            for chunk in snapshot_service.chunked(rows):
                response = await client.post(
                    f"{identity.cloud_url}/sync/stock/changes", json={"rows": chunk}, headers=_headers(identity),
                )
                if response.status_code >= 400:
                    problem = (
                        f"Head office refused the stock for items that moved ({response.status_code}). "
                        "Its item stock stays as it was until this works or the next full push."
                    )
                    break
                body = response.json()
                if body.get("needsFull"):
                    # Not a fault: head office has no complete list to update yet, so the full push
                    # on the slow loop is the thing that has to happen, and it will.
                    break
                sent += int(body.get("applied") or 0)
            else:
                # Only a clean sweep of every chunk may move the mark — a run that stopped partway
                # must leave the items it never sent looking unsent.
                new_mark = (mark, started)
    except httpx.HTTPError as exc:
        problem = (
            f"No connection to head office while sending stock for items that moved ({type(exc).__name__}). "
            "It will go again next time."
        )
    finally:
        # Fresh read: a full run may have moved the mark meanwhile, and only this run's own result is saved.
        latest = await SyncState.get(id=IDENTITY_PK)
        latest.running = False
        fields = ["running"]
        if new_mark and new_mark[0] >= latest.stock_mark:
            latest.stock_mark, latest.stock_mark_at = new_mark
            fields += ["stock_mark", "stock_mark_at"]
        if problem:
            latest.last_error = problem
            fields.append("last_error")
        await latest.save(update_fields=fields)
    if problem:
        _note_sync_error(triggered_by, problem)
    elif sent:
        print(f"  sync [{triggered_by}]: stock for {sent} item(s) sent to head office", flush=True)
    return sent


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
                    "have been revoked. Nothing has been sent."
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
                await _mark_failed(unconfirmed, "Head office did not confirm these events.", refused=True)
                # The queue is ordered by age, so an event head office will never store sits at the
                # front of every batch from now on and nothing behind it ever leaves the branch. After
                # a few refusals it is set aside: still here, with the reason on it, but out of the
                # queue, so the next tick gets past it. Nothing is deleted — the ledger still answers
                # "did that sale reach head office?", and now it answers "no, and here is why".
                set_aside = [e for e in unconfirmed if e.attempt_count >= MAX_REFUSALS_PER_EVENT]
                for event in set_aside:
                    event.status = "held"
                    event.last_error = (
                        f"Head office refused this event {event.attempt_count} times, so it has been set "
                        "aside to let the rest of the queue go. It is still here and can be sent again "
                        "once head office can take it."
                    )[:500]
                    await event.save(update_fields=["status", "last_error"])
                if set_aside:
                    logs.log.warning(
                        "sync: %s event(s) head office would not store, set aside after %s refusals (first: %s %s, event %s)",
                        len(set_aside), MAX_REFUSALS_PER_EVENT,
                        set_aside[0].aggregate_type, set_aside[0].aggregate_id, set_aside[0].id,
                    )
                result.set_aside += len(set_aside)
                result.error = (
                    f"{len(set_aside)} event(s) head office would not store have been set aside so the "
                    f"rest of the queue can go; see the log for which."
                    if set_aside else
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
        # Read before the rows are built: anything that moves while they're built or sent goes again
        # with the next quick push rather than being missed.
        mark, mark_at = await snapshot_service.movement_mark(), datetime.now(timezone.utc)
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
            result.stock_mark, result.stock_mark_at = mark, mark_at
    except httpx.HTTPError:
        result.error = result.error or (
            "No connection to head office while sending this branch's figures. "
            "Its previous figures are unchanged and this will go again next time."
        )


async def _mark_failed(events: list[OutboxEvent], reason: str, *, refused: bool = False) -> None:
    """Record the attempt without losing the event. `failed` is picked up again by the next run —
    it means "tried and didn't land", not "give up".

    `attempt_count` moves only when head office actually looked at the event and didn't take it
    (`refused`). A batch that never got there — no link, a timeout, credentials rejected, head office
    refusing the whole call — leaves it alone, because that count is what decides an event is
    unstorable, and a branch offline for a week must not spend its events' chances on a dead line.
    `last_attempt_at` and `last_error` still record every try, so "when did this last go out" is
    answerable either way."""
    now = datetime.now(timezone.utc)
    fields = ["status", "last_attempt_at", "last_error"] + (["attempt_count"] if refused else [])
    for event in events:
        event.status = "failed"
        if refused:
            event.attempt_count += 1
        event.last_attempt_at = now
        event.last_error = reason[:500]
        await event.save(update_fields=fields)
