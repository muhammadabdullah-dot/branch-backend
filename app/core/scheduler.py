"""The thing that makes sync happen without anybody asking it to.

It lives in the branch server process rather than in the browser, and that is the whole point. A
branch closes at eleven at night and the last terminal gets locked; if syncing depended on someone
having a tab open, the day's takings would reach head office whenever somebody next happened to
log in. The server is the only thing in the branch that is reliably awake, so it is the thing that
carries the schedule.

The loop is deliberately dull. It wakes, tries, records, sleeps. It never raises out of the task —
a scheduler that dies on an unexpected exception stops syncing silently, which is the worst of all
outcomes because everything looks fine right up until someone asks for last month's figures.
"""
import asyncio
import contextlib
from datetime import datetime, timezone

from app.core.config import settings

_task: asyncio.Task | None = None


async def _tick() -> int:
    """One attempt. Returns how many seconds to wait before the next one."""
    # Imported here rather than at module scope: the scheduler starts inside the app's startup
    # hook, and pulling the ORM-backed services in at import time would bind them before Tortoise
    # has initialized.
    from app.services import registration_service, sync_service

    identity = await registration_service.current()
    if identity is None:
        # Not verified yet. Check back reasonably soon — this is exactly the window in which
        # somebody is standing at the terminal typing the key in.
        return min(settings.sync_retry_seconds, 300)

    result = await sync_service.run_once(triggered_by="scheduler")
    if result.ok and not result.skipped_reason:
        return settings.sync_interval_seconds
    # Skipped because a manual run was already going: that run is doing the work, so just resume
    # the normal rhythm rather than piling on.
    if result.skipped_reason:
        return settings.sync_interval_seconds
    return settings.sync_retry_seconds


async def _loop() -> None:
    await asyncio.sleep(settings.sync_startup_delay_seconds)
    while True:
        delay = settings.sync_retry_seconds
        try:
            delay = await _tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the loop must outlive anything one tick can do
            print(f"  sync scheduler: tick failed ({type(exc).__name__}: {exc})", flush=True)
        await asyncio.sleep(max(delay, 30))


def start() -> None:
    global _task
    if not settings.sync_enabled:
        print("  sync scheduler: disabled (SYNC_ENABLED=false)", flush=True)
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="branch-sync-scheduler")
    hours = settings.sync_interval_seconds / 3600
    print(
        f"  sync scheduler: every {hours:g}h "
        f"(retrying every {settings.sync_retry_seconds // 60}m while offline), "
        f"first run in {settings.sync_startup_delay_seconds}s",
        flush=True,
    )


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _task
    _task = None


def is_running() -> bool:
    return _task is not None and not _task.done()


def next_run_hint() -> str:
    """A plain sentence for the status screen. Deliberately a hint, not a countdown — the loop
    sleeps on a timer it does not publish, and inventing a precise time we cannot honour would be
    worse than saying roughly."""
    if not settings.sync_enabled:
        return "Automatic sync is switched off on this server."
    hours = settings.sync_interval_seconds / 3600
    return f"Runs automatically every {hours:g} hours, and keeps retrying while the connection is down."


def now() -> datetime:
    return datetime.now(timezone.utc)
