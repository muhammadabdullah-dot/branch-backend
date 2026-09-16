from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.middlewares.auth import require_permission
from app.models import User
from app.services import purchase_report_service, sales_report_service

router = APIRouter(prefix="/reports", tags=["reports"])

_read = require_permission("reports", "R")


@router.get("/sales-summary")
async def sales_summary(
    groupBy: str = "user", from_: datetime | None = Query(None, alias="from"), to: datetime | None = None,
    dayStartHour: int = Query(0, ge=0, le=23), user: User = Depends(_read),
) -> dict:
    """The legacy User Wise Sale Summary. `groupBy`: user, date, customer, item, brand, category,
    department, itemClass, manufacturer, supplier. `dayStartHour` sets when a shop day begins for the
    date grouping (8 means 08:00 to 07:59 the next morning); the window itself is `from`/`to`."""
    return await sales_report_service.summary(groupBy, from_, to, dayStartHour)


@router.get("/purchase-summary")
async def purchase_summary(
    groupBy: str = "brand", view: str = "summary", from_: datetime | None = Query(None, alias="from"), to: datetime | None = None,
    approved: str = Query("all", pattern="^(all|yes|no)$"), supplierId: str | None = None, user: User = Depends(_read),
) -> dict:
    """The legacy Purchase Summary Group Wise. `groupBy`: brand, category, department, itemClass, manufacturer,
    supplier, item. `view`: summary, detail (Items under each group) or item (Item-wise with GST). `approved`
    narrows deliveries to approved or unapproved ones."""
    return await purchase_report_service.summary(groupBy, view, from_, to, approved, supplierId)
