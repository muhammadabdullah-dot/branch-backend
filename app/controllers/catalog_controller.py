from datetime import datetime

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.models import Product, ProductAlias, ProductSupplier, User
from app.schemas.catalog import (
    CatalogFacetsOut, LastPurchaseOut, PriceChangeOut, ProductAliasIn, ProductAliasOut, ProductCreate,
    ProductDetailOut, ProductListOut, ProductOut, ProductRef, ProductSupplierIn, ProductSupplierOut, ProductUpdate,
)
from app.schemas.import_result import ImportSummary
from app.services import catalog_service, media_service


def _alias_out(a: ProductAlias) -> ProductAliasOut:
    return ProductAliasOut(code=a.code, remarks=a.remarks, qty=a.qty, discPercent=a.disc_percent, discFlat=a.disc_flat)


def _fields(p: Product, include_aliases: bool = False) -> dict:
    return dict(
        id=p.id, sku=p.sku, name=p.name, price=p.price, taxRate=p.tax_rate, isWeighed=p.is_weighed,
        unit=p.unit, barcode=p.barcode, packUnit=p.pack_unit, packSize=p.pack_size, avgCost=p.avg_cost,
        rpp=p.rpp, department=p.department, category=p.category, itemClass=p.item_class,
        subclass=p.subclass, manufacturer=p.manufacturer, brand=p.brand, active=p.active,
        aliases=[_alias_out(a) for a in p.aliases] if include_aliases else [],
        discPercent=p.disc_percent, discFlat=p.disc_flat, lockDisc=p.lock_disc, variant=p.variant,
        origin=p.origin, remarks=p.remarks, hasPicture=bool(p.picture), parentId=p.parent_id, parentQty=p.parent_qty,
        wholesalePrice=p.wholesale_price, reorderLevel=p.reorder_level,
    )


def _to_out(p: Product, include_aliases: bool = False) -> ProductOut:
    return ProductOut(**_fields(p, include_aliases))


def _supplier_out(link: ProductSupplier) -> ProductSupplierOut:
    return ProductSupplierOut(
        supplierId=link.supplier.id, supplierCode=link.supplier.code, supplierName=link.supplier.name,
        priority=link.priority, active=link.supplier.active,
    )


def _raise(exc: catalog_service.CatalogError) -> None:
    raise HTTPException(exc.status, exc.message)


async def list_all(
    q: str | None, limit: int, offset: int, ids: list[str] | None = None,
    sort: str | None = None, order: str | None = None, supplier_id: str | None = None,
) -> ProductListOut:
    items, total = await catalog_service.list_all(q, limit, offset, ids, sort, order, supplier_id)
    return ProductListOut(items=[_to_out(p, include_aliases=True) for p in items], total=total)


async def detail(product_id: str) -> ProductDetailOut:
    try:
        product, suppliers, parent, children, last = await catalog_service.detail(product_id)
    except catalog_service.CatalogError as exc:
        _raise(exc)
    return ProductDetailOut(
        **_fields(product, include_aliases=True),
        suppliers=[_supplier_out(s) for s in suppliers],
        parent=ProductRef(id=parent.id, sku=parent.sku, name=parent.name) if parent else None,
        children=[ProductRef(id=c.id, sku=c.sku, name=c.name, parentQty=c.parent_qty) for c in children],
        lastPurchase=LastPurchaseOut(
            unitPrice=last.unit_price, discPercent=last.disc_percent, at=last.grn.at,
            grnNumber=last.grn.grn_number, supplierName=last.grn.supplier.name,
        ) if last else None,
    )


async def create(data: ProductCreate, user: User) -> ProductOut:
    try:
        return _to_out(await catalog_service.create(data, user))
    except catalog_service.CatalogError as exc:
        _raise(exc)


async def update(product_id: str, data: ProductUpdate, user: User) -> ProductOut:
    try:
        return _to_out(await catalog_service.update(product_id, data, user))
    except catalog_service.CatalogError as exc:
        _raise(exc)


async def replace_aliases(product_id: str, aliases: list[ProductAliasIn]) -> list[ProductAliasOut]:
    try:
        return [_alias_out(a) for a in await catalog_service.replace_aliases(product_id, aliases)]
    except catalog_service.CatalogError as exc:
        _raise(exc)


async def replace_suppliers(product_id: str, links: list[ProductSupplierIn]) -> list[ProductSupplierOut]:
    try:
        return [_supplier_out(s) for s in await catalog_service.replace_suppliers(product_id, links)]
    except catalog_service.CatalogError as exc:
        _raise(exc)


async def set_picture(product_id: str, content: bytes) -> ProductOut:
    try:
        return _to_out(await catalog_service.set_picture(product_id, content))
    except catalog_service.CatalogError as exc:
        _raise(exc)


async def remove_picture(product_id: str) -> ProductOut:
    try:
        return _to_out(await catalog_service.remove_picture(product_id))
    except catalog_service.CatalogError as exc:
        _raise(exc)


async def picture(product_id: str) -> FileResponse:
    product = await Product.get_or_none(id=product_id)
    found = media_service.picture_file(product.picture if product else None)
    if not found:
        raise HTTPException(404, "No picture for this Item")
    path, content_type = found
    return FileResponse(path, media_type=content_type, headers={"Cache-Control": "private, max-age=300"})


async def facets() -> CatalogFacetsOut:
    return CatalogFacetsOut(**await catalog_service.facets())


async def price_changes(from_at: datetime | None, to_at: datetime | None) -> list[PriceChangeOut]:
    return [
        PriceChangeOut(
            productId=c.product.id, sku=c.product.sku, name=c.product.name, barcode=c.product.barcode,
            oldPrice=c.old_price, newPrice=c.new_price, oldRpp=c.old_rpp, newRpp=c.new_rpp, source=c.source, at=c.at,
        )
        for c in await catalog_service.price_changes(from_at, to_at)
    ]


async def import_file(filename: str, content: bytes) -> ImportSummary:
    return await catalog_service.import_products(filename, content)


async def import_aliases_file(filename: str, content: bytes) -> ImportSummary:
    return await catalog_service.import_product_aliases(filename, content)


async def lookup(code: str) -> ProductOut | None:
    found = await catalog_service.lookup_by_code(code)
    if not found:
        return None
    product, alias = found
    await product.fetch_related("aliases")
    out = _to_out(product, include_aliases=True)
    out.matchedAlias = _alias_out(alias) if alias else None
    return out
