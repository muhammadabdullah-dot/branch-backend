from tortoise.transactions import atomic

from app.models import OutboxEvent, Transfer, User


class TransferError(Exception):
    def __init__(self, message: str):
        self.message = message


async def list_all() -> list[Transfer]:
    return await Transfer.all().prefetch_related("lines").order_by("-requested_at")


@atomic()
async def open_dispute(user: User, transfer_id: str, note: str) -> Transfer:
    transfer = await Transfer.get_or_none(id=transfer_id)
    if not transfer:
        raise TransferError("Transfer not found")
    transfer.dispute_open = True
    transfer.dispute_note = note
    await transfer.save()
    await OutboxEvent.create(
        aggregate_type="Transfer", aggregate_id=str(transfer.id),
        payload={"event": "dispute_opened", "note": note}, origin_user_id=str(user.id),
    )
    await transfer.fetch_related("lines")
    return transfer
