from app.models import Product
from app.schemas.catalog import ProductAliasOut, ProductCreate, ProductListOut, ProductOut
from app.schemas.import_result import ImportSummary
from app.services import catalog_service


def _to_out(p: Product, include_aliases: bool = False) -> ProductOut:
    aliases = [ProductAliasOut(code=a.code, remarks=a.remarks) for a in p.aliases] if include_aliases else []
    return ProductOut(
        id=p.id, sku=p.sku, name=p.name, price=p.price, taxRate=p.tax_rate, isWeighed=p.is_weighed,
        unit=p.unit, barcode=p.barcode, packUnit=p.pack_unit, packSize=p.pack_size, avgCost=p.avg_cost,
        rpp=p.rpp, department=p.department, category=p.category, itemClass=p.item_class,
        subclass=p.subclass, manufacturer=p.manufacturer, brand=p.brand, active=p.active, aliases=aliases,
    )


async def list_all(q: str | None, limit: int, offset: int, ids: list[str] | None = None) -> ProductListOut:
    items, total = await catalog_service.list_all(q, limit, offset, ids)
    return ProductListOut(items=[_to_out(p, include_aliases=True) for p in items], total=total)


async def create(data: ProductCreate) -> ProductOut:
    return _to_out(await catalog_service.upsert(data))


async def import_file(filename: str, content: bytes) -> ImportSummary:
    return await catalog_service.import_products(filename, content)


async def import_aliases_file(filename: str, content: bytes) -> ImportSummary:
    return await catalog_service.import_product_aliases(filename, content)


async def lookup(code: str) -> ProductOut | None:
    product = await catalog_service.lookup_by_code(code)
    if not product:
        return None
    await product.fetch_related("aliases")
    return _to_out(product, include_aliases=True)
