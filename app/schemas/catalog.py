from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.types import Money


class ProductAliasOut(BaseModel):
    code: str
    remarks: str | None = None


class ProductOut(BaseModel):
    id: str
    sku: str
    name: str
    price: Money
    taxRate: Decimal
    isWeighed: bool
    unit: str
    barcode: str | None = None
    packUnit: str | None = None
    packSize: int | None = None
    avgCost: Money
    rpp: Money | None = None
    department: str | None = None
    category: str | None = None
    itemClass: str | None = None
    subclass: str | None = None
    manufacturer: str | None = None
    brand: str | None = None
    active: bool
    aliases: list[ProductAliasOut] = []


class ProductListOut(BaseModel):
    items: list[ProductOut]
    total: int


class ProductCreate(BaseModel):
    sku: str
    name: str
    price: Decimal
    taxRate: Decimal = Decimal("0")
    isWeighed: bool = False
    unit: str = "pc"
    barcode: str | None = None
    packUnit: str | None = None
    packSize: int | None = None
    avgCost: Decimal | None = None
    rpp: Decimal | None = None
    department: str | None = None
    category: str | None = None
    itemClass: str | None = None
    subclass: str | None = None
    manufacturer: str | None = None
    brand: str | None = None
    active: bool = True


class SupplierOut(BaseModel):
    id: str
    code: str
    name: str
    contactPerson: str | None = None
    phone: str | None = None


class SupplierCreate(BaseModel):
    code: str
    name: str
    contactPerson: str | None = None
    phone: str | None = None
