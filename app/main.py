from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from tortoise.contrib.fastapi import register_tortoise

from app.core import scheduler
from app.core.config import TORTOISE_ORM, settings
from app.core.frontend import FrontendMiddleware, default_dist
from app.middlewares.activity import ActivityMiddleware
from app.routes.accounts import router as accounts_router
from app.routes.alerts import router as alerts_router
from app.core.network import get_lan_ip
from app.middlewares.device import device_identity_middleware
from app.core import logs
from app.middlewares.error_handler import register_error_handlers
from app.routes.client_errors import router as client_errors_router
from app.routes.counters import router as counters_router
from app.routes.auth import router as auth_router
from app.routes.catalog import router as catalog_router
from app.routes.gift_vouchers import router as gift_vouchers_router
from app.routes.health import router as health_router
from app.routes.held_bills import router as held_bills_router
from app.routes.inventory import router as inventory_router
from app.routes.exports import router as exports_router
from app.routes.locations import router as locations_router
from app.routes.maintenance import router as maintenance_router
from app.routes.masters import router as masters_router
from app.routes.requisitions import router as stock_requests_router
from app.routes.transfers import router as transfers_router
from app.routes.members import router as members_router
from app.routes.purchase_orders import router as purchase_orders_router
from app.routes.reports import router as reports_router
from app.routes.parties import router as parties_router
from app.routes.rbac import router as rbac_router
from app.routes.rbac import users_router
from app.routes.registration import router as registration_router
from app.routes.registration import sync_router
from app.routes.sales import router as sales_router
from app.routes.returns import router as returns_router
from app.routes.suppliers import router as suppliers_router
from app.routes.till import router as till_router
from app.services.gift_voucher_service import link_named_vouchers
from app.services.seed_service import ensure_payment_methods, revise_legacy_roles, seed_if_empty, sync_role_labels, sync_role_resource_grants

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
# The built branch-app on this same port — added last so it's the outermost layer: a browser opening a
# page gets the app before any API route or middleware sees the request.
FRONTEND_DIST = None if settings.frontend_dir.lower() == "off" else (Path(settings.frontend_dir) if settings.frontend_dir else default_dist("branch-app"))
app.middleware("http")(device_identity_middleware)

register_error_handlers(app)
logs.setup()


@app.on_event("startup")
async def _start_log() -> None:
    """First of the startup steps, so anything that fails while starting is in the log too."""
    path = logs.setup()
    logs.log.info("%s starting on port %s (log: %s)", settings.app_name, settings.port, path)


@app.on_event("shutdown")
async def _end_log() -> None:
    logs.log.info("%s stopped", settings.app_name)

app.include_router(health_router)
app.include_router(registration_router)
app.include_router(sync_router)
app.include_router(auth_router)
app.include_router(rbac_router)
app.include_router(users_router)
app.include_router(parties_router)
app.include_router(till_router)
app.include_router(counters_router)
app.include_router(sales_router)
app.include_router(returns_router)
app.include_router(held_bills_router)
app.include_router(gift_vouchers_router)
app.include_router(exports_router)
app.include_router(locations_router)
app.include_router(purchase_orders_router)
app.include_router(reports_router)
app.include_router(inventory_router)
app.include_router(stock_requests_router)
app.include_router(transfers_router)
app.include_router(catalog_router)
app.include_router(suppliers_router)
app.include_router(members_router)
app.include_router(masters_router)
app.include_router(alerts_router)
app.include_router(accounts_router)
from app.routes import fixed_assets, tax_reports  # noqa: E402
app.include_router(fixed_assets.router)
app.include_router(tax_reports.router)
app.include_router(maintenance_router)
app.include_router(client_errors_router)

register_tortoise(app, config=TORTOISE_ORM, generate_schemas=False, add_exception_handlers=True)


@app.on_event("startup")
async def _seed() -> None:
    await seed_if_empty()
    await ensure_payment_methods()
    revised = await revise_legacy_roles()
    if revised:
        print(f"  staff: {revised} account(s) moved onto per-person access", flush=True)
    await sync_role_labels()
    from app.services.seed_service import split_the_books_access

    split = await split_the_books_access()
    if split:
        print(f"  accounts: {split} account(s) moved onto a tick per accounts screen and area", flush=True)
    await sync_role_resource_grants()
    await link_named_vouchers()
    from app.services import counter_service

    await counter_service.ensure_default_counter()
    from app.services.seed_service import give_managers_the_counter_board

    boarded = await give_managers_the_counter_board()
    if boarded:
        print(f"  counters: {boarded} Branch Manager account(s) can now put people on counters", flush=True)
    from app.services.login_session_service import give_managers_the_sign_ins

    signed = await give_managers_the_sign_ins()
    if signed:
        print(f"  logins: {signed} Branch Manager account(s) can now see who is signed in and end a login", flush=True)
    from app.services import accounts_chart_service, staff_sync_service
    from app.services.seed_service import give_branch_managers_the_books

    added = await accounts_chart_service.ensure_standard_chart()
    if added:
        print(f"  accounts: {added} chart row(s) added", flush=True)
    await accounts_chart_service.ensure_party_accounts()
    given = await give_branch_managers_the_books()
    if given:
        print(f"  accounts: {given} Branch Manager account(s) given the books", flush=True)
    reported = await staff_sync_service.backfill()
    if reported:
        print(f"  staff sync: {reported} account(s) queued for head office", flush=True)


@app.on_event("startup")
async def _start_scheduler() -> None:
    """The sync loop belongs to the server, not to a browser tab — see app/core/scheduler.py. It
    starts whether or not this branch is verified yet; an unverified branch simply finds nothing to
    do and checks again shortly, which is what makes a branch start syncing the moment somebody
    finishes typing the key in rather than after the next restart."""
    scheduler.start()


@app.on_event("startup")
async def _start_posting() -> None:
    """The books post themselves from the branch's records every few minutes, sync or no sync."""
    from app.core import accounts_scheduler

    accounts_scheduler.start()


@app.on_event("startup")
async def _start_backups() -> None:
    """The daily backup, taken by the server itself."""
    from app.core import backup_scheduler

    backup_scheduler.start()


@app.on_event("shutdown")
async def _stop_scheduler() -> None:
    from app.core import accounts_scheduler, backup_scheduler

    await backup_scheduler.stop()
    await accounts_scheduler.stop()
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
    if FRONTEND_DIST is not None:
        found = (FRONTEND_DIST / "index.html").is_file()
        print(
            f"  App:     {'served from ' + str(FRONTEND_DIST) if found else 'not built yet, so run npm run build in branch-app'}\n",
            flush=True,
        )


# Every action, by whom — inside the frontend layer, so pages the app itself serves aren't recorded.
app.add_middleware(ActivityMiddleware)


@app.middleware("http")
async def _paused_for_restore(request, call_next):
    """While a backup is being put back, nothing else reads or writes the database."""
    from app.services import backup_service

    if backup_service.RESTORING and not request.url.path.startswith(("/health", "/maintenance/status")):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "The database is being restored from a backup. Try again in a minute."}, status_code=503)
    return await call_next(request)


if FRONTEND_DIST is not None:
    app.add_middleware(FrontendMiddleware, dist=FRONTEND_DIST)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port)
