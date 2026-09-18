"""Product catalog — CRUD plus bulk CSV/XLSX/XLS import so real catalogs (tens of thousands of
SKUs, per the real legacy exports this was built against, 2026-09-11) don't have to be typed in
one at a time. Recognizes the actual legacy column names (ITEM NAME, BARCODE, RPP, PURCHASE
PRICE, SALES/SALE PRICE, DEPARTMENT, CATEGORY, CLASS, SUBCLASS/SUB CLASS, MANUFACTURER, BRAND,
PACK SIZE/PACK UNIT, STATUS) alongside our own simpler column names — real column headers
disagree with each other across the two real export files, hence the synonym lookups throughout.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from tortoise.expressions import Q
from tortoise.transactions import atomic

from app.models import GRNLine, Product, ProductAlias, ProductPriceChange, ProductSupplier, Supplier, User
from app.schemas.catalog import ProductAliasIn, ProductCreate, ProductSupplierIn, ProductUpdate
from app.schemas.import_result import ImportRowError, ImportSummary
from app.services import media_service, price_history_service
from app.services.import_service import cell_bool, cell_str_any, parse_rows, row_error


class CatalogError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


_FIELD_MAP = {
    "taxRate": "tax_rate", "isWeighed": "is_weighed", "packUnit": "pack_unit",
    "packSize": "pack_size", "avgCost": "avg_cost", "itemClass": "item_class",
    "discPercent": "disc_percent", "discFlat": "disc_flat", "lockDisc": "lock_disc",
    "parentId": "parent_id", "parentQty": "parent_qty",
    "wholesalePrice": "wholesale_price", "reorderLevel": "reorder_level",
    "packsPerBox": "packs_per_box", "packPrice": "pack_price", "boxPrice": "box_price",
    "piecesPerUnit": "pieces_per_unit", "piecesPerStrip": "pieces_per_strip", "pieceUnit": "piece_unit",
    "piecePrice": "piece_price", "stripPrice": "strip_price",
}
_TEXT_FIELDS = {
    "name", "unit", "barcode", "packUnit", "department", "category", "itemClass", "subclass",
    "manufacturer", "brand", "variant", "remarks", "pieceUnit",
}
# Columns a form update may never set to NULL, whatever the request says.
_REQUIRED_COLUMNS = {"name", "price", "tax_rate", "is_weighed", "unit", "active", "disc_percent", "disc_flat", "lock_disc"}


def _to_model_fields(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        if key == "sku":
            continue
        if key in _TEXT_FIELDS and isinstance(value, str):
            value = value.strip() or None
        out[_FIELD_MAP.get(key, key)] = value
    return out


# Sortable catalog columns. An allow-list: the key comes from the browser, and `order_by` on an
# unchecked string would accept any field name at all.
PRODUCT_SORTS = {
    "sku": "sku", "name": "name", "brand": "brand", "category": "category",
    "price": "price", "taxRate": "tax_rate", "unit": "unit", "avgCost": "avg_cost",
}


async def list_all(
    q: str | None, limit: int, offset: int, ids: list[str] | None = None,
    sort: str | None = None, order: str | None = None, supplier_id: str | None = None, needs_details: bool | None = None,
) -> tuple[list[Product], int]:
    """Paginated + searchable — a real catalog runs into the tens of thousands of rows (this was
    built against a 47k-row real export), so 'fetch everything' isn't just slow, it's structurally
    broken: SQLite errors on 'too many SQL variables' prefetching a reverse relation for that many
    parent rows in one IN(...) clause. Bounded pages sidestep that entirely.

    `ids` resolves a known set of products in one call — what a caller holding product *ids*
    (a recalled held bill, say) needs, since neither search nor the code lookup can find a row
    by its id."""
    qs = Product.all()
    if ids:
        qs = qs.filter(id__in=ids)
    if supplier_id:
        qs = qs.filter(supplier_links__supplier_id=supplier_id).distinct()
    if needs_details is not None:
        qs = qs.filter(needs_details=needs_details)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode__icontains=q))
    total = await qs.count()
    # Sorted in the database, not the browser: this is a 47,000-row catalog shown fifty at a time, so
    # a browser sort would only reorder the fifty on screen. `id` breaks ties so paging never repeats
    # or skips a product that shares a price or a brand with its neighbour.
    field = PRODUCT_SORTS.get(sort or "name", "name")
    direction = "-" if order == "desc" else ""
    items = await qs.order_by(f"{direction}{field}", "id").offset(offset).limit(limit).prefetch_related("aliases")
    return items, total


# -- Codes ------------------------------------------------------------------------------------
#
# Alias code (sku), barcode and alternate barcodes are what a salesperson types or scans. They are
# three columns, but one namespace at the till: if a code belonged to two Items the scan would ring
# up whichever the lookup happened to try first. So every write checks a new code against all three.


async def code_owner(code: str, except_product_id: str | None = None) -> Product | None:
    """The Item that already answers to `code` as its alias code, barcode or an alternate barcode."""
    code = code.strip()
    for qs in (Product.filter(sku=code), Product.filter(barcode=code)):
        if except_product_id:
            qs = qs.exclude(id=except_product_id)
        hit = await qs.first()
        if hit:
            return hit
    alias_qs = ProductAlias.filter(code=code)
    if except_product_id:
        alias_qs = alias_qs.exclude(product_id=except_product_id)
    alias = await alias_qs.prefetch_related("product").first()
    return alias.product if alias else None


async def _refuse_taken(code: str, what: str, except_product_id: str | None = None) -> None:
    owner = await code_owner(code, except_product_id)
    if owner:
        raise CatalogError(f"{what} {code} already belongs to {owner.name} ({owner.sku}).")


async def _next_sku() -> str:
    """One past the highest short numeric alias code in use; the legacy codes are numbers like 166247.

    Only codes of up to seven digits count. Some imported Items carry their 13-digit barcode as their
    code, and continuing from one of those would hand every new Item a barcode-length number."""
    highest = 0
    for sku in await Product.all().values_list("sku", flat=True):
        if sku.isdigit() and len(sku) <= 7:
            highest = max(highest, int(sku))
    candidate = highest + 1
    while await code_owner(str(candidate)):
        candidate += 1
    return str(candidate)


async def _check_parent(product_id: str | None, parent_id: str | None, parent_qty: Decimal | None) -> None:
    if not parent_id:
        return
    if parent_id == product_id:
        raise CatalogError("An Item can't be its own parent pack.")
    parent = await Product.get_or_none(id=parent_id)
    if not parent:
        raise CatalogError("That parent Item doesn't exist.")
    if not parent_qty:
        raise CatalogError(f"Say how many of this Item make one {parent.name}.")
    # Walk up from the parent: reaching this Item again would make a loop (A inside B inside A).
    seen = {parent_id}
    current = parent
    while current.parent_id:
        if current.parent_id == product_id or current.parent_id in seen:
            raise CatalogError(f"{parent.name} is already packed inside this Item, which would make a loop.")
        seen.add(current.parent_id)
        current = await Product.get(id=current.parent_id)


# Every price, cost and discount change is logged by price_history_service: a snapshot before, a row per value
# that moved after.


@atomic()
async def create(data: ProductCreate, user: User | None = None) -> Product:
    """From the Item form: refuses a code or barcode that already rings up another Item. Imports
    use `import_products`, which updates by code instead."""
    sku = (data.sku or "").strip() or await _next_sku()
    await _refuse_taken(sku, "Code")
    fields = _to_model_fields(data.model_dump())
    await _refuse_switched_off(fields)
    if fields.get("barcode"):
        if fields["barcode"] == sku:
            fields["barcode"] = None
        else:
            await _refuse_taken(fields["barcode"], "Barcode")
    await _check_parent(None, fields.get("parent_id"), fields.get("parent_qty"))
    if not fields.get("parent_id"):
        fields["parent_qty"] = None
    fields["avg_cost"] = fields.get("avg_cost") or Decimal("0")
    product = await Product.create(id=sku, sku=sku, **fields)
    # A new Item's first price starts its price history, and counts as a price to put on a shelf tag.
    await price_history_service.save_rows([price_history_service.first_price(product, "new-item", None, user)])
    return product


@atomic()
async def update(product_id: str, data: ProductUpdate, user: User | None = None) -> Product:
    product = await Product.get_or_none(id=product_id)
    if not product:
        raise CatalogError("Item not found", status=404)
    before = price_history_service.snapshot(product)
    changes = _to_model_fields(data.model_dump(exclude_unset=True))
    await _refuse_switched_off(changes, product)

    if changes.get("barcode"):
        if changes["barcode"] == product.sku:
            changes["barcode"] = None
        else:
            await _refuse_taken(changes["barcode"], "Barcode", except_product_id=product.id)
    if "parent_id" in changes or "parent_qty" in changes:
        parent_id = changes.get("parent_id", product.parent_id)
        parent_qty = changes.get("parent_qty", product.parent_qty)
        await _check_parent(product.id, parent_id, parent_qty)
        if not parent_id:
            changes["parent_qty"] = None

    for column, value in changes.items():
        if column in _REQUIRED_COLUMNS and value is None:
            continue
        setattr(product, column, value)
    # The whole Item form was saved (it always sends the name), so an Item written in by hand is no longer waiting.
    if "name" in changes:
        product.needs_details = False
    await product.save()
    await price_history_service.record(product, before, "form", None, user)
    return product


async def detail(product_id: str):
    product = await Product.get_or_none(id=product_id).prefetch_related("aliases")
    if not product:
        raise CatalogError("Item not found", status=404)
    suppliers = await ProductSupplier.filter(product_id=product.id).prefetch_related("supplier").order_by("priority")
    parent = await Product.get_or_none(id=product.parent_id) if product.parent_id else None
    children = await Product.filter(parent_id=product.id).order_by("name")
    last = await GRNLine.filter(product_id=product.id).order_by("-grn__at").prefetch_related("grn__supplier").first()
    return product, suppliers, parent, children, last


@atomic()
async def replace_aliases(product_id: str, aliases: list[ProductAliasIn]) -> list[ProductAlias]:
    """Saves the whole Alternate Barcode grid: what's sent replaces what was there."""
    product = await Product.get_or_none(id=product_id)
    if not product:
        raise CatalogError("Item not found", status=404)
    seen: set[str] = set()
    for alias in aliases:
        code = alias.code.strip()
        if code in seen:
            raise CatalogError(f"Barcode {code} is listed twice.")
        if code in (product.sku, product.barcode):
            raise CatalogError(f"{code} is already this Item's own code or barcode.")
        seen.add(code)
        await _refuse_taken(code, "Barcode", except_product_id=product.id)
    await ProductAlias.filter(product_id=product.id).delete()
    return [
        await ProductAlias.create(
            product=product, code=a.code.strip(), remarks=(a.remarks or "").strip() or None,
            qty=a.qty, disc_percent=a.discPercent, disc_flat=a.discFlat,
        )
        for a in aliases
    ]


@atomic()
async def replace_suppliers(product_id: str, links: list[ProductSupplierIn]) -> list[ProductSupplier]:
    product = await Product.get_or_none(id=product_id)
    if not product:
        raise CatalogError("Item not found", status=404)
    ids = [link.supplierId for link in links]
    if len(set(ids)) != len(ids):
        raise CatalogError("A supplier is listed twice.")
    found = {s.id: s for s in await Supplier.filter(id__in=ids)}
    missing = [i for i in ids if i not in found]
    if missing:
        raise CatalogError(f"Unknown supplier {missing[0]}")
    await ProductSupplier.filter(product_id=product.id).delete()
    for link in links:
        await ProductSupplier.create(product=product, supplier=found[link.supplierId], priority=link.priority)
    return await ProductSupplier.filter(product_id=product.id).prefetch_related("supplier").order_by("priority")


async def set_picture(product_id: str, content: bytes) -> Product:
    product = await Product.get_or_none(id=product_id)
    if not product:
        raise CatalogError("Item not found", status=404)
    try:
        product.picture = media_service.save_picture("items", product.id, content, replacing=product.picture)
    except media_service.MediaError as exc:
        raise CatalogError(exc.message)
    await product.save(update_fields=["picture"])
    return product


async def remove_picture(product_id: str) -> Product:
    product = await Product.get_or_none(id=product_id)
    if not product:
        raise CatalogError("Item not found", status=404)
    media_service.remove_picture(product.picture)
    product.picture = None
    await product.save(update_fields=["picture"])
    return product


async def facets() -> dict[str, list[str]]:
    """What the Item form offers: the switched-on values of each Item list (Branch Console > Lists and Settings), which
    also holds every value already on an Item. Variants aren't a list; they describe one Item."""
    from app.services import masters_service

    lists = await masters_service.choices()
    variants = await Product.filter(variant__isnull=False).distinct().order_by("variant").limit(2000).values_list("variant", flat=True)
    return {
        "departments": lists["department"], "categories": lists["category"], "classes": lists["class"],
        "subclasses": lists["subclass"], "manufacturers": lists["manufacturer"], "brands": lists["brand"],
        "units": lists["unit"], "packUnits": lists["pack-unit"], "gstRates": lists["gst-rate"],
        "variants": [v for v in variants if v and v.strip()],
    }


async def _refuse_switched_off(fields: dict, product: Product | None = None) -> None:
    from app.services import masters_service

    before = {column: getattr(product, column) for column in masters_service.ITEM_FIELD_KINDS} if product else None
    try:
        await masters_service.refuse_switched_off_values("products", fields, before)
    except masters_service.MastersError as exc:
        raise CatalogError(exc.message) from exc


async def price_changes(from_at: datetime | None, to_at: datetime | None, limit: int = 500) -> list[ProductPriceChange]:
    """Real sale and retail price moves, plus each new Item's first price. A save that left the price alone is never
    recorded, so this list is exactly the shelf tags that need reprinting. Cost, wholesale and discount changes don't
    touch a shelf tag, and Items added from a file are left out (a first import adds tens of thousands)."""
    shelf = Q(field__in=list(price_history_service.SHELF_FIELDS)) | Q(field__isnull=True)
    qs = ProductPriceChange.filter(shelf).exclude(source="import-new")
    if from_at:
        qs = qs.filter(at__gte=from_at)
    if to_at:
        qs = qs.filter(at__lt=to_at)
    return await qs.order_by("-at").limit(limit).prefetch_related("product")


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

    tax_raw = cell_str_any(row, "taxRate", "TAX RATE")
    wholesale_raw = cell_str_any(row, "wholesalePrice", "WHOLESALE RATE")
    reorder_raw = cell_str_any(row, "reorderLevel", "MIN STOCK", "MINIMUM STOCK")
    # Only the cells the file actually has go into the model, so an update can tell "this column
    # wasn't in the file" (leave the Item alone) from "set this".
    present = {
        "taxRate": Decimal(tax_raw) if tax_raw else None,
        "isWeighed": cell_bool(row, "isWeighed") if cell_str_any(row, "isWeighed") else None,
        "unit": cell_str_any(row, "unit"),
        "barcode": barcode,
        "packUnit": cell_str_any(row, "packUnit"),
        # A pack size of 0 in the legacy files means "not sold in packs".
        "packSize": (int(float(pack_size_raw)) or None) if pack_size_raw else None,
        "avgCost": Decimal(avg_cost_raw) if avg_cost_raw else None,
        "rpp": Decimal(rpp_raw) if rpp_raw else None,
        "wholesalePrice": Decimal(wholesale_raw) if wholesale_raw else None,
        "reorderLevel": Decimal(reorder_raw) if reorder_raw else None,
        "department": cell_str_any(row, "department", "DEPARTMENT"),
        "category": cell_str_any(row, "category", "CATEGORY"),
        "itemClass": cell_str_any(row, "itemClass", "CLASS"),
        "subclass": cell_str_any(row, "subclass", "SUBCLASS", "SUB CLASS"),
        "manufacturer": cell_str_any(row, "manufacturer", "MANUFACTURER"),
        "brand": cell_str_any(row, "brand", "BRAND"),
        "active": (status.strip().upper() != "N") if status else None,
    }
    return ProductCreate(sku=sku, name=name, price=Decimal(price), **{k: v for k, v in present.items() if v is not None})


async def import_products(filename: str, content: bytes, user: User | None = None) -> ImportSummary:
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
            if data.sku in existing_skus:
                # An update writes only what the file filled. A price list with just code, name and
                # price must not wipe brands, pack sizes and costs already on record.
                to_update.append((data.sku, _to_model_fields(data.model_dump(exclude_unset=True))))
            else:
                fields = _to_model_fields(data.model_dump())
                fields["avg_cost"] = fields.get("avg_cost") or Decimal("0")
                to_create.append(Product(id=data.sku, sku=data.sku, **fields))
        except (ValueError, InvalidOperation, KeyError) as exc:
            errors.append(ImportRowError(row=i, message=row_error(exc)))

    # Price history is collected across the whole file and written in one bulk insert at the end.
    file_name = (filename or "").replace("\\", "/").split("/")[-1] or None
    history = []
    if to_create:
        await Product.bulk_create(to_create, batch_size=500)
        history += [price_history_service.first_price(p, "import-new", file_name, user) for p in to_create]
    for sku, fields in to_update:
        product = await Product.get(sku=sku)
        before = price_history_service.snapshot(product)
        for key, value in fields.items():
            setattr(product, key, value)
        await product.save()
        history += price_history_service.rows_for(product, before, "import", file_name, user)
    await price_history_service.save_rows(history)

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
                raise ValueError(f"No product named {name!r}, so import products before aliases")
            seen_in_file.add(code)
            to_create.append(
                ProductAlias(product_id=product_id, code=code, remarks=cell_str_any(row, "Remarks", "remarks"))
            )
        except (ValueError, KeyError) as exc:
            errors.append(ImportRowError(row=i, message=row_error(exc)))

    if to_create:
        await ProductAlias.bulk_create(to_create, batch_size=500)
    return ImportSummary(created=len(to_create), updated=0, errors=errors)


async def lookup_by_code(code: str) -> tuple[Product, ProductAlias | None] | None:
    """Resolves any of sku / barcode / alias code to one product — the real data proved these
    are three separate, non-overlapping namespaces a cashier might scan or type against. When the
    code was an alternate barcode, that alias comes back too: it carries the pack quantity."""
    normalized = code.strip()
    product = await Product.get_or_none(sku=normalized)
    if product:
        return product, None
    product = await Product.get_or_none(barcode=normalized)
    if product:
        return product, None
    alias = await ProductAlias.get_or_none(code=normalized).prefetch_related("product")
    return (alias.product, alias) if alias else None
