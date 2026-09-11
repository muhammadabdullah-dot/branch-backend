"""Product catalog — CRUD plus bulk CSV/XLSX import so real catalogs (hundreds/thousands of SKUs)
don't have to be typed in one at a time during initial setup and testing.
"""
from decimal import Decimal, InvalidOperation

from app.models import Product
from app.schemas.catalog import ProductCreate
from app.schemas.import_result import ImportRowError, ImportSummary
from app.services.import_service import cell_bool, cell_str, parse_rows

_FIELD_MAP = {"taxRate": "tax_rate", "isWeighed": "is_weighed", "packUnit": "pack_unit", "packSize": "pack_size"}


async def list_all() -> list[Product]:
    return await Product.all().order_by("name")


async def upsert(data: ProductCreate) -> Product:
    fields = {_FIELD_MAP.get(k, k): v for k, v in data.model_dump().items() if k != "sku"}
    product = await Product.get_or_none(sku=data.sku)
    if product:
        for key, value in fields.items():
            setattr(product, key, value)
        await product.save()
        return product
    return await Product.create(id=data.sku, sku=data.sku, **fields)


async def import_products(filename: str, content: bytes) -> ImportSummary:
    rows = parse_rows(filename, content)
    created = 0
    updated = 0
    errors: list[ImportRowError] = []
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        try:
            sku = cell_str(row, "sku")
            name = cell_str(row, "name")
            price = cell_str(row, "price")
            unit = cell_str(row, "unit")
            if not sku or not name or not price or not unit:
                raise ValueError("sku, name, price, and unit are required")
            data = ProductCreate(
                sku=sku,
                name=name,
                price=Decimal(price),
                taxRate=Decimal(cell_str(row, "taxRate") or "0"),
                isWeighed=cell_bool(row, "isWeighed"),
                unit=unit,
                barcode=cell_str(row, "barcode"),
                packUnit=cell_str(row, "packUnit"),
                packSize=int(cell_str(row, "packSize")) if cell_str(row, "packSize") else None,
            )
            existed = await Product.exists(sku=data.sku)
            await upsert(data)
            if existed:
                updated += 1
            else:
                created += 1
        except (ValueError, InvalidOperation, KeyError) as exc:
            errors.append(ImportRowError(row=i, message=str(exc)))
    return ImportSummary(created=created, updated=updated, errors=errors)
