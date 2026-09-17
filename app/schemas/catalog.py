from datetime import datetime
from decimal import Decimal

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.types import Money


Origin = Literal["local", "imported"]


class ProductAliasOut(BaseModel):
    code: str
    remarks: str | None = None
    qty: Decimal = Decimal("1")
    discPercent: Decimal = Decimal("0")
    discFlat: Money = Decimal("0")


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
    discPercent: Decimal = Decimal("0")
    discFlat: Money = Decimal("0")
    lockDisc: bool = False
    variant: str | None = None
    origin: str | None = None
    remarks: str | None = None
    hasPicture: bool = False
    parentId: str | None = None
    parentQty: Decimal | None = None
    # Blank: wholesale bills take the branch's wholesale discount off the sale price.
    wholesalePrice: Money | None = None
    # Blank: the branch's usual low stock level.
    reorderLevel: Decimal | None = None
    # Set only by the code lookup, when the code scanned was an alternate barcode: that pack's
    # quantity and discount, so a carton scan can ring up the carton rather than one unit.
    matchedAlias: ProductAliasOut | None = None


class ProductListOut(BaseModel):
    items: list[ProductOut]
    total: int


class ProductRef(BaseModel):
    id: str
    sku: str
    name: str
    parentQty: Decimal | None = None


class ProductSupplierOut(BaseModel):
    supplierId: str
    supplierCode: str
    supplierName: str
    priority: int
    active: bool


class LastPurchaseOut(BaseModel):
    unitPrice: Money
    discPercent: Decimal
    at: datetime
    grnNumber: str
    supplierName: str


class ProductDetailOut(ProductOut):
    suppliers: list[ProductSupplierOut] = []
    parent: ProductRef | None = None
    children: list[ProductRef] = []
    lastPurchase: LastPurchaseOut | None = None


class ProductCreate(BaseModel):
    # Blank on the form means "number it for me". Imports always carry one.
    sku: str | None = Field(default=None, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    price: Decimal = Field(ge=0)
    taxRate: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    isWeighed: bool = False
    unit: str = Field(default="pc", min_length=1, max_length=20)
    barcode: str | None = Field(default=None, max_length=40)
    packUnit: str | None = Field(default=None, max_length=40)
    packSize: int | None = Field(default=None, ge=1)
    avgCost: Decimal | None = None
    rpp: Decimal | None = Field(default=None, ge=0)
    department: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)
    itemClass: str | None = Field(default=None, max_length=80)
    subclass: str | None = Field(default=None, max_length=80)
    manufacturer: str | None = Field(default=None, max_length=120)
    brand: str | None = Field(default=None, max_length=120)
    active: bool = True
    discPercent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    discFlat: Decimal = Field(default=Decimal("0"), ge=0)
    lockDisc: bool = False
    variant: str | None = Field(default=None, max_length=60)
    origin: Origin | None = None
    remarks: str | None = Field(default=None, max_length=255)
    parentId: str | None = None
    parentQty: Decimal | None = Field(default=None, gt=0)
    wholesalePrice: Decimal | None = Field(default=None, ge=0)
    reorderLevel: Decimal | None = Field(default=None, ge=0)


class ProductUpdate(BaseModel):
    """Only fields sent change. The alias code (sku) is the Item's identity on every sale, GRN and
    count, so it stays; average cost comes from receiving, never from a form."""
    name: str | None = Field(default=None, min_length=1, max_length=160)
    price: Decimal | None = Field(default=None, ge=0)
    taxRate: Decimal | None = Field(default=None, ge=0, le=100)
    isWeighed: bool | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    barcode: str | None = Field(default=None, max_length=40)
    packUnit: str | None = Field(default=None, max_length=40)
    packSize: int | None = Field(default=None, ge=1)
    rpp: Decimal | None = Field(default=None, ge=0)
    department: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)
    itemClass: str | None = Field(default=None, max_length=80)
    subclass: str | None = Field(default=None, max_length=80)
    manufacturer: str | None = Field(default=None, max_length=120)
    brand: str | None = Field(default=None, max_length=120)
    active: bool | None = None
    discPercent: Decimal | None = Field(default=None, ge=0, le=100)
    discFlat: Decimal | None = Field(default=None, ge=0)
    lockDisc: bool | None = None
    variant: str | None = Field(default=None, max_length=60)
    origin: Origin | None = None
    remarks: str | None = Field(default=None, max_length=255)
    parentId: str | None = None
    parentQty: Decimal | None = Field(default=None, gt=0)
    wholesalePrice: Decimal | None = Field(default=None, ge=0)
    reorderLevel: Decimal | None = Field(default=None, ge=0)


class ProductAliasIn(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    remarks: str | None = Field(default=None, max_length=255)
    qty: Decimal = Field(default=Decimal("1"), gt=0)
    discPercent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    discFlat: Decimal = Field(default=Decimal("0"), ge=0)


class ProductSupplierIn(BaseModel):
    supplierId: str
    priority: int = Field(default=1, ge=1, le=99)


class CatalogFacetsOut(BaseModel):
    """Values already in use, offered as suggestions on the Item form so "Nestle" and "NESTLE " don't
    become two brands."""
    departments: list[str]
    categories: list[str]
    classes: list[str]
    subclasses: list[str]
    manufacturers: list[str]
    brands: list[str]
    units: list[str]
    packUnits: list[str]
    # Switched-on GST rates, written the short way ("0", "17", "18").
    gstRates: list[str] = []
    variants: list[str]


class PriceChangeOut(BaseModel):
    productId: str
    sku: str
    name: str
    barcode: str | None = None
    oldPrice: Money
    newPrice: Money
    oldRpp: Money | None = None
    newRpp: Money | None = None
    source: str
    at: datetime


class SupplierOut(BaseModel):
    id: str
    code: str
    name: str
    contactPerson: str | None = None
    phone: str | None = None
    phone2: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    ntn: str | None = None
    sTaxRegNo: str | None = None
    cnic: str | None = None
    dueDays: int = 0
    discountPercent: Money = Decimal("0")
    remarks: str | None = None
    active: bool = True
    # The company list: head office's identity for it (empty until head office has it), and where it was added.
    companyId: str | None = None
    # "head-office", "this-branch" or "other-branch"
    origin: str = "this-branch"
    originName: str | None = None


class SupplierFields(BaseModel):
    contactPerson: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    phone2: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=180)
    address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=80)
    ntn: str | None = Field(default=None, max_length=40)
    sTaxRegNo: str | None = Field(default=None, max_length=40)
    cnic: str | None = Field(default=None, max_length=40)
    dueDays: int = Field(default=0, ge=0, le=365)
    discountPercent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    remarks: str | None = Field(default=None, max_length=255)


class SupplierCreate(SupplierFields):
    # Blank on the form means "number it for me" (SUP0001, SUP0002 …). Imports always carry one.
    code: str | None = Field(default=None, max_length=20)
    name: str = Field(min_length=1, max_length=160)


class SupplierUpdate(BaseModel):
    """Only the fields sent change. The code is the supplier's identity on every GRN, so it stays."""
    name: str | None = Field(default=None, min_length=1, max_length=160)
    contactPerson: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    phone2: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=180)
    address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=80)
    ntn: str | None = Field(default=None, max_length=40)
    sTaxRegNo: str | None = Field(default=None, max_length=40)
    cnic: str | None = Field(default=None, max_length=40)
    dueDays: int | None = Field(default=None, ge=0, le=365)
    discountPercent: Decimal | None = Field(default=None, ge=0, le=100)
    remarks: str | None = Field(default=None, max_length=255)
    active: bool | None = None
