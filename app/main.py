from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from tortoise.contrib.fastapi import register_tortoise

from app.core import scheduler
from app.core.config import TORTOISE_ORM, settings
from app.core.network import get_lan_ip
from app.middlewares.device import device_identity_middleware
from app.middlewares.error_handler import register_error_handlers
from app.routes.auth import router as auth_router
from app.routes.catalog import router as catalog_router
from app.routes.gift_vouchers import router as gift_vouchers_router
from app.routes.health import router as health_router
from app.routes.held_bills import router as held_bills_router
from app.routes.inventory import router as inventory_router
from app.routes.parties import router as parties_router
from app.routes.rbac import router as rbac_router
from app.routes.rbac import users_router
from app.routes.registration import router as registration_router
from app.routes.registration import sync_router
from app.routes.sales import router as sales_router
from app.routes.suppliers import router as suppliers_router
from app.routes.till import router as till_router
from app.services.seed_service import seed_if_empty, sync_role_labels, sync_role_resource_grants

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(device_identity_middleware)

register_error_handlers(app)
app.include_router(health_router)
app.include_router(registration_router)
app.include_router(sync_router)
app.include_router(auth_router)
app.include_router(rbac_router)
app.include_router(users_router)
app.include_router(parties_router)
app.include_router(till_router)
app.include_router(sales_router)
app.include_router(held_bills_router)
app.include_router(gift_vouchers_router)
app.include_router(inventory_router)
app.include_router(catalog_router)
app.include_router(suppliers_router)

register_tortoise(app, config=TORTOISE_ORM, generate_schemas=False, add_exception_handlers=True)


@app.on_event("startup")
async def _seed() -> None:
    await seed_if_empty()
    await sync_role_labels()
    await sync_role_resource_grants()


@app.on_event("startup")
async def _start_scheduler() -> None:
    """The sync loop belongs to the server, not to a browser tab — see app/core/scheduler.py. It
    starts whether or not this branch is verified yet; an unverified branch simply finds nothing to
    do and checks again shortly, which is what makes a branch start syncing the moment somebody
    finishes typing the key in rather than after the next restart."""
    scheduler.start()


@app.on_event("shutdown")
async def _stop_scheduler() -> None:
    await scheduler.stop()


@app.on_event("startup")
async def _print_banner() -> None:
    lan_ip = get_lan_ip()
    print(
        "\n"
        f"  {settings.app_name}\n"
        f"  Local:   http://127.0.0.1:{settings.port}\n"
        f"  Network: http://{lan_ip}:{settings.port}   (for other devices on this LAN)\n",
        flush=True,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port)
