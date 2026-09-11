"""Per-request device id, set once by the device middleware and read anywhere a service writes
an OutboxEvent — avoids threading a device_id parameter through every service function."""
from contextvars import ContextVar

_device_id: ContextVar[str | None] = ContextVar("device_id", default=None)


def set_device_id(device_id: str | None) -> None:
    _device_id.set(device_id)


def get_device_id() -> str | None:
    return _device_id.get()
