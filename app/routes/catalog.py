from datetime import datetime

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.controllers import catalog_controller, price_history_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.catalog import (
    CatalogFacetsOut, PriceChangeOut, ProductAliasIn, ProductAliasOut, ProductCreate, ProductDetailOut,
    ProductListOut, ProductOut, ProductSupplierIn, ProductSupplierOut, ProductUpdate,
)
from app.schemas.import_result import ImportSummary
from app.schemas.price_history import PriceChangeChoicesOut, PriceChangesReportOut, PriceHistoryEntryOut

router = APIRouter(prefix="/catalog/products", tags=["catalog"])

# Reading the item master is not an inventory-only act: you cannot ring up a sale, recall a held
# bill or scan a barcode without it. Gating reads on `inventory.catalog` alone is what made the
# counter roles fall back to a 10-row fixture and report real barcodes as "item not found".
# Granting them `inventory.catalog` instead would have put the Product Catalog *screen* in a
# cashier's nav, which is a different and wrong answer: writes stay inventory-only.
_read = require_any_permission(("inventory.catalog", "R"), ("store.billing", "R"))
_write = require_permission("inventory.catalog", "W")
# Labels reprints shelf tags from the price-change log.
_price_log_read = require_any_permission(("inventory.catalog", "R"), ("inventory.labels", "R"))


@router.get("", response_model=ProductListOut)
async def list_products(
    q: str | None = None, ids: str | None = None, limit: int = 50, offset: int = 0,
    sort: str | None = None, order: str | None = None, supplierId: str | None = None, needsDetails: bool | None = None,
    user: User = Depends(_read),
) -> ProductListOut:
    """`sort` is one of sku, name, brand, category, price, taxRate, unit, avgCost; `order` is asc
    (default) or desc. `supplierId` narrows to the Items linked to that supplier on the Item form."""
    # `ids` is a comma-separated set the caller already knows it needs; the page cap still applies,
    # so a caller asking for more than 200 at once gets a page of them, not a silent truncation.
    id_list = [i for i in (ids.split(",") if ids else []) if i][:200]
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    return await catalog_controller.list_all(q, limit, offset, id_list or None, sort, order, supplierId, needsDetails)


@router.post("", response_model=ProductOut)
async def create_product(payload: ProductCreate, user: User = Depends(_write)) -> ProductOut:
    """Add one Item. Leave `sku` out to have the next code assigned. Refuses a code or barcode that
    already rings up another Item; bulk updates go through /import."""
    return await catalog_controller.create(payload, user)


@router.post("/import", response_model=ImportSummary)
async def import_products(file: UploadFile = File(...), user: User = Depends(_write)) -> ImportSummary:
    content = await file.read()
    return await catalog_controller.import_file(file.filename, content, user)


@router.post("/import-aliases", response_model=ImportSummary)
async def import_aliases(file: UploadFile = File(...), user: User = Depends(_write)) -> ImportSummary:
    content = await file.read()
    return await catalog_controller.import_aliases_file(file.filename, content)


@router.get("/lookup", response_model=ProductOut | None)
async def lookup_product(code: str, user: User = Depends(_read)) -> ProductOut | None:
    return await catalog_controller.lookup(code)


@router.get("/facets", response_model=CatalogFacetsOut)
async def facets(user: User = Depends(_read)) -> CatalogFacetsOut:
    return await catalog_controller.facets()


@router.get("/price-changes", response_model=list[PriceChangeOut])
async def price_changes(
    from_: datetime | None = Query(None, alias="from"), to: datetime | None = None, user: User = Depends(_price_log_read),
) -> list[PriceChangeOut]:
    """Items whose sale or retail price changed in the window, newest first (legacy Check New Price List)."""
    return await catalog_controller.price_changes(from_, to)


# Price history: the Price Changes report (its own tick), and one Item's history on the Item form.
_price_report = require_permission("reports.price-changes", "R")
_item_history = require_any_permission(("inventory.catalog", "R"), ("reports.price-changes", "R"))


@router.get("/price-history", response_model=PriceChangesReportOut)
async def price_history_report(
    from_: datetime | None = Query(None, alias="from"), to: datetime | None = None, department: str | None = None,
    userId: str | None = None, source: str | None = None, field: str | None = None, q: str | None = None,
    limit: int = 100, offset: int = 0, user: User = Depends(_price_report),
) -> PriceChangesReportOut:
    """Every change to a sale, retail or wholesale price, cost or Item discount in the window, newest first.
    `userId=none` is changes nobody made by hand. `limit` up to 20000 for an export."""
    return await price_history_controller.report(
        from_, to, department, userId, source, field, q, min(max(limit, 1), 20000), max(offset, 0),
    )


@router.get("/price-history/choices", response_model=PriceChangeChoicesOut)
async def price_history_choices(user: User = Depends(_price_report)) -> PriceChangeChoicesOut:
    return await price_history_controller.choices()


@router.get("/{product_id}/price-history", response_model=list[PriceHistoryEntryOut])
async def item_price_history(product_id: str, user: User = Depends(_item_history)) -> list[PriceHistoryEntryOut]:
    """One Item's price history, newest first."""
    return await price_history_controller.for_item(product_id)


# The screens that flag low stock (dashboard alerts, Stock Overview) work from the stock ledger, not the catalog, so
# they read the levels on their own: only Items that have one, plus the usual level for the rest.
_levels_read = require_any_permission(
    ("inventory.overview", "R"), ("inventory.catalog", "R"), ("reports", "R"), ("branch-console.dashboard", "R"),
)


@router.get("/reorder-levels")
async def reorder_levels(user: User = Depends(_levels_read)) -> dict:
    """{usualLevel, levels: {productId: level}}. Low stock is fewer than the Item's level, else fewer than the usual one."""
    from app.models import Product
    from app.services import masters_service

    rows = await Product.filter(reorder_level__isnull=False).values_list("id", "reorder_level")
    usual = await masters_service.low_stock_level()
    return {"usualLevel": format(usual.normalize(), "f"), "levels": {pid: format(level.normalize(), "f") for pid, level in rows}}


@router.get("/on-hand")
async def on_hand(ids: str = Query(..., max_length=20000), user: User = Depends(_read)) -> dict[str, str]:
    """{productId: what the branch holds across its switched-on locations}. Billing asks before adding to a line, because
    stock never goes below zero: what isn't here can't be sold."""
    from app.services import stock_guard

    wanted = [i for i in dict.fromkeys(x.strip() for x in ids.split(",")) if i][:500]
    held = await stock_guard.on_hand(wanted)
    return {pid: stock_guard.qty_text(qty) for pid, qty in held.items()}


# Paths with an id come last, so /lookup, /facets, /price-changes and /reorder-levels and /on-hand are never read as an id.
@router.get("/{product_id}", response_model=ProductDetailOut)
async def detail(product_id: str, user: User = Depends(_read)) -> ProductDetailOut:
    return await catalog_controller.detail(product_id)


@router.patch("/{product_id}", response_model=ProductOut)
async def update_product(product_id: str, payload: ProductUpdate, user: User = Depends(_write)) -> ProductOut:
    return await catalog_controller.update(product_id, payload, user)


@router.put("/{product_id}/aliases", response_model=list[ProductAliasOut])
async def replace_aliases(product_id: str, payload: list[ProductAliasIn], user: User = Depends(_write)) -> list[ProductAliasOut]:
    """Saves the whole Alternate Barcode grid: the list sent replaces what was there."""
    return await catalog_controller.replace_aliases(product_id, payload)


@router.put("/{product_id}/suppliers", response_model=list[ProductSupplierOut])
async def replace_suppliers(product_id: str, payload: list[ProductSupplierIn], user: User = Depends(_write)) -> list[ProductSupplierOut]:
    return await catalog_controller.replace_suppliers(product_id, payload)


@router.get("/{product_id}/picture")
async def picture(product_id: str, user: User = Depends(_read)):
    return await catalog_controller.picture(product_id)


@router.put("/{product_id}/picture", response_model=ProductOut)
async def upload_picture(product_id: str, file: UploadFile = File(...), user: User = Depends(_write)) -> ProductOut:
    return await catalog_controller.set_picture(product_id, await file.read())


@router.delete("/{product_id}/picture", response_model=ProductOut)
async def delete_picture(product_id: str, user: User = Depends(_write)) -> ProductOut:
    return await catalog_controller.remove_picture(product_id)
