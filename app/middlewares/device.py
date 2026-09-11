"""Registers/touches the calling device on every request (X-Device-Id header) and stashes it in
a contextvar for services to stamp on OutboxEvent — the endpoint-identity requirement from the
architecture doc §4.2, applied cross-cuttingly rather than as a per-route dependency."""
from app.core.device_context import set_device_id
from app.models import Device


async def device_identity_middleware(request, call_next):
    device_id = request.headers.get("x-device-id")
    if device_id:
        device_id = device_id.strip()[:80]
        device, created = await Device.get_or_create(id=device_id)
        if not created:
            await device.save(update_fields=["last_seen_at"])
        set_device_id(device_id)
    else:
        set_device_id(None)
    return await call_next(request)
