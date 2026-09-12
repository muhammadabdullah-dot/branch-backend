from fastapi import APIRouter, Depends, File, UploadFile

from app.controllers import catalog_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.catalog import ProductCreate, ProductListOut, ProductOut
from app.schemas.import_result import ImportSummary

router = APIRouter(prefix="/catalog/products", tags=["catalog"])

# Reading the item master is not an inventory-only act: you cannot ring up a sale, recall a held
# bill or scan a barcode without it. Gating reads on `inventory.catalog` alone is what made the
# counter roles fall back to a 10-row fixture and report real barcodes as "item not found".
# Granting them `inventory.catalog` instead would have put the Product Catalog *screen* in a
# cashier's nav, which is a different and wrong answer — writes stay inventory-only.
_read = require_any_permission(("inventory.catalog", "R"), ("store.billing", "R"))
_write = require_permission("inventory.catalog", "W")


@router.get("", response_model=ProductListOut)
async def list_products(
    q: str | None = None, ids: str | None = None, limit: int = 50, offset: int = 0, user: User = Depends(_read)
) -> ProductListOut:
    # `ids` is a comma-separated set the caller already knows it needs; the page cap still applies,
    # so a caller asking for more than 200 at once gets a page of them, not a silent truncation.
    id_list = [i for i in (ids.split(",") if ids else []) if i][:200]
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    return await catalog_controller.list_all(q, limit, offset, id_list or None)


@router.post("", response_model=ProductOut)
async def create_product(payload: ProductCreate, user: User = Depends(_write)) -> ProductOut:
    return await catalog_controller.create(payload)


@router.post("/import", response_model=ImportSummary)
async def import_products(file: UploadFile = File(...), user: User = Depends(_write)) -> ImportSummary:
    content = await file.read()
    return await catalog_controller.import_file(file.filename, content)


@router.post("/import-aliases", response_model=ImportSummary)
async def import_aliases(file: UploadFile = File(...), user: User = Depends(_write)) -> ImportSummary:
    content = await file.read()
    return await catalog_controller.import_aliases_file(file.filename, content)


@router.get("/lookup", response_model=ProductOut | None)
async def lookup_product(code: str, user: User = Depends(_read)) -> ProductOut | None:
    return await catalog_controller.lookup(code)
