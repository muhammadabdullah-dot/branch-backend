from decimal import Decimal

from pydantic import BaseModel

from app.schemas.types import Money


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


class ProductCreate(BaseModel):
    sku: str
    name: str
    price: Decimal
    taxRate: Decimal = Decimal("0")
    isWeighed: bool = False
    unit: str
    barcode: str | None = None
    packUnit: str | None = None
    packSize: int | None = None


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
