"""Posting the books on a timer, inside the branch server — like sync, it can't depend on someone having a screen open."""
import asyncio
import contextlib
import os

from app.core import logs

_task: asyncio.Task | None = None
INTERVAL = int(os.environ.get("ACCOUNTS_POSTING_SECONDS", "300"))


async def _loop() -> None:
    from app.services import accounts_posting_service

    await asyncio.sleep(int(os.environ.get("ACCOUNTS_POSTING_DELAY_SECONDS", "45")))
    while True:
        try:
            await accounts_posting_service.run(full=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the loop must outlive anything one run can do
            logs.log.error("accounts posting: run failed", exc_info=exc)
        await asyncio.sleep(max(INTERVAL, 60))


def start() -> None:
    global _task
    if os.environ.get("ACCOUNTS_POSTING", "on").lower() == "off":
        print("  accounts posting: switched off (ACCOUNTS_POSTING=off)", flush=True)
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="accounts-posting")


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
    _task = None
