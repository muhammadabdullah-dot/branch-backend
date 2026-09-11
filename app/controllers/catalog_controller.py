from app.models import Product
from app.schemas.catalog import ProductCreate, ProductOut
from app.schemas.import_result import ImportSummary
from app.services import catalog_service


def _to_out(p: Product) -> ProductOut:
    return ProductOut(
        id=p.id, sku=p.sku, name=p.name, price=p.price, taxRate=p.tax_rate, isWeighed=p.is_weighed,
        unit=p.unit, barcode=p.barcode, packUnit=p.pack_unit, packSize=p.pack_size, avgCost=p.avg_cost,
    )


async def list_all() -> list[ProductOut]:
    return [_to_out(p) for p in await catalog_service.list_all()]


async def create(data: ProductCreate) -> ProductOut:
    return _to_out(await catalog_service.upsert(data))


async def import_file(filename: str, content: bytes) -> ImportSummary:
    return await catalog_service.import_products(filename, content)
