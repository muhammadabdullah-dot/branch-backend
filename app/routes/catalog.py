from fastapi import APIRouter, Depends, File, UploadFile

from app.controllers import catalog_controller
from app.middlewares.auth import require_permission
from app.models import User
from app.schemas.catalog import ProductCreate, ProductListOut, ProductOut
from app.schemas.import_result import ImportSummary

router = APIRouter(prefix="/catalog/products", tags=["catalog"])

_read = require_permission("inventory.catalog", "R")
_write = require_permission("inventory.catalog", "W")


@router.get("", response_model=ProductListOut)
async def list_products(
    q: str | None = None, limit: int = 50, offset: int = 0, user: User = Depends(_read)
) -> ProductListOut:
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    return await catalog_controller.list_all(q, limit, offset)


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
