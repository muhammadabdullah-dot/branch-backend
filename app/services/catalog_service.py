"""Product catalog — CRUD plus bulk CSV/XLSX/XLS import so real catalogs (tens of thousands of
SKUs, per the real legacy exports this was built against, 2026-09-11) don't have to be typed in
one at a time. Recognizes the actual legacy column names (ITEM NAME, BARCODE, RPP, PURCHASE
PRICE, SALES/SALE PRICE, DEPARTMENT, CATEGORY, CLASS, SUBCLASS/SUB CLASS, MANUFACTURER, BRAND,
PACK SIZE/PACK UNIT, STATUS) alongside our own simpler column names — real column headers
disagree with each other across the two real export files, hence the synonym lookups throughout.
"""
from decimal import Decimal, InvalidOperation

from tortoise.expressions import Q

from app.models import Product, ProductAlias
from app.schemas.catalog import ProductCreate
from app.schemas.import_result import ImportRowError, ImportSummary
from app.services.import_service import cell_bool, cell_str_any, parse_rows

_FIELD_MAP = {
    "taxRate": "tax_rate", "isWeighed": "is_weighed", "packUnit": "pack_unit",
    "packSize": "pack_size", "avgCost": "avg_cost", "itemClass": "item_class",
}


def _to_model_fields(data: dict) -> dict:
    return {_FIELD_MAP.get(k, k): v for k, v in data.items() if k != "sku"}


async def list_all(q: str | None, limit: int, offset: int) -> tuple[list[Product], int]:
    """Paginated + searchable — a real catalog runs into the tens of thousands of rows (this was
    built against a 47k-row real export), so 'fetch everything' isn't just slow, it's structurally
    broken: SQLite errors on 'too many SQL variables' prefetching a reverse relation for that many
    parent rows in one IN(...) clause. Bounded pages sidestep that entirely."""
    qs = Product.all()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode__icontains=q))
    total = await qs.count()
    items = await qs.order_by("name").offset(offset).limit(limit).prefetch_related("aliases")
    return items, total


async def upsert(data: ProductCreate) -> Product:
    fields = _to_model_fields(data.model_dump())
    product = await Product.get_or_none(sku=data.sku)
    if product:
        for key, value in fields.items():
            setattr(product, key, value)
        await product.save()
        return product
    return await Product.create(id=data.sku, sku=data.sku, **fields)


def _row_to_product_create(row: dict) -> ProductCreate:
    barcode = cell_str_any(row, "barcode", "BARCODE")
    sku = cell_str_any(row, "sku") or barcode
    name = cell_str_any(row, "name", "ITEM NAME")
    price = cell_str_any(row, "price", "SALES PRICE", "SALE PRICE")
    if not sku or not name or price is None:
        raise ValueError("Need at minimum a sku-or-barcode, a name, and a price")

    pack_size_raw = cell_str_any(row, "packSize", "PACK SIZE", "PACK UNIT")
    rpp_raw = cell_str_any(row, "rpp", "RPP")
    avg_cost_raw = cell_str_any(row, "avgCost", "PURCHASE PRICE")
    status = cell_str_any(row, "STATUS")  # only present by name in one of the two real files

    return ProductCreate(
        sku=sku,
        name=name,
        price=Decimal(price),
        taxRate=Decimal(cell_str_any(row, "taxRate", "TAX RATE") or "0"),
        isWeighed=cell_bool(row, "isWeighed"),
        unit=cell_str_any(row, "unit") or "pc",
        barcode=barcode,
        packUnit=cell_str_any(row, "packUnit"),
        packSize=int(float(pack_size_raw)) if pack_size_raw else None,
        avgCost=Decimal(avg_cost_raw) if avg_cost_raw else None,
        rpp=Decimal(rpp_raw) if rpp_raw else None,
        department=cell_str_any(row, "department", "DEPARTMENT"),
        category=cell_str_any(row, "category", "CATEGORY"),
        itemClass=cell_str_any(row, "itemClass", "CLASS"),
        subclass=cell_str_any(row, "subclass", "SUBCLASS", "SUB CLASS"),
        manufacturer=cell_str_any(row, "manufacturer", "MANUFACTURER"),
        brand=cell_str_any(row, "brand", "BRAND"),
        active=(status.strip().upper() != "N") if status else True,
    )


async def import_products(filename: str, content: bytes) -> ImportSummary:
    """Bulk-inserts new products (Product.bulk_create) rather than one create() per row —
    necessary at real-catalog scale (tens of thousands of rows); updates to already-existing
    skus stay per-row since a first import is almost always all-creates."""
    rows = parse_rows(filename, content)
    existing_skus = set(await Product.all().values_list("sku", flat=True))
    to_create: list[Product] = []
    to_update: list[tuple[str, dict]] = []
    seen_in_file: set[str] = set()
    errors: list[ImportRowError] = []

    for i, row in enumerate(rows, start=2):  # row 1 is the header
        try:
            data = _row_to_product_create(row)
            if data.sku in seen_in_file:
                raise ValueError(f"Duplicate sku/barcode {data.sku} within this file")
            seen_in_file.add(data.sku)
            fields = _to_model_fields(data.model_dump())
            if data.sku in existing_skus:
                to_update.append((data.sku, fields))
            else:
                to_create.append(Product(id=data.sku, sku=data.sku, **fields))
        except (ValueError, InvalidOperation, KeyError) as exc:
            errors.append(ImportRowError(row=i, message=str(exc)))

    if to_create:
        await Product.bulk_create(to_create, batch_size=500)
    for sku, fields in to_update:
        product = await Product.get(sku=sku)
        for key, value in fields.items():
            setattr(product, key, value)
        await product.save()

    return ImportSummary(created=len(to_create), updated=len(to_update), errors=errors)


async def import_product_aliases(filename: str, content: bytes) -> ImportSummary:
    """The legacy AliasName/Name/Remarks shape (real file: 1,793 rows, 19% of matched products
    have 2+ aliases — different pack sizes of the same bulk item). Resolves Name against
    Product.name (case/whitespace-normalized) since the alias file carries no product id of its
    own to join on — import products before aliases, or every row here will fail to resolve."""
    rows = parse_rows(filename, content)
    products = await Product.all().values("id", "name")
    name_to_id = {p["name"].strip().upper(): p["id"] for p in products}

    existing_codes = set(await ProductAlias.all().values_list("code", flat=True))
    to_create: list[ProductAlias] = []
    seen_in_file: set[str] = set()
    errors: list[ImportRowError] = []

    for i, row in enumerate(rows, start=2):
        try:
            code = cell_str_any(row, "AliasName", "code")
            name = cell_str_any(row, "Name", "name")
            if not code or not name:
                raise ValueError("AliasName and Name are both required")
            if code in seen_in_file or code in existing_codes:
                raise ValueError(f"Alias code {code} already exists")
            product_id = name_to_id.get(name.strip().upper())
            if not product_id:
                raise ValueError(f"No product named {name!r} — import products before aliases")
            seen_in_file.add(code)
            to_create.append(
                ProductAlias(product_id=product_id, code=code, remarks=cell_str_any(row, "Remarks", "remarks"))
            )
        except (ValueError, KeyError) as exc:
            errors.append(ImportRowError(row=i, message=str(exc)))

    if to_create:
        await ProductAlias.bulk_create(to_create, batch_size=500)
    return ImportSummary(created=len(to_create), updated=0, errors=errors)


async def lookup_by_code(code: str) -> Product | None:
    """Resolves any of sku / barcode / alias code to one product — the real data proved these
    are three separate, non-overlapping namespaces a cashier might scan or type against."""
    normalized = code.strip()
    product = await Product.get_or_none(sku=normalized)
    if product:
        return product
    product = await Product.get_or_none(barcode=normalized)
    if product:
        return product
    alias = await ProductAlias.get_or_none(code=normalized).prefetch_related("product")
    return alias.product if alias else None
