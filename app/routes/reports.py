from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.services import branch_analytics_service, branch_kpi_service, purchase_report_service, sales_report_service

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


# ── ABC and XYZ, and the branch figures you can open up ─────────────────────────────────────────
# Periods arrive as a name (`last6m`) or as `custom` with `from` and `to` shop days, and are resolved on the server so
# the same link means the same days to everyone who opens it.

_analysis = require_permission("reports.analysis", "R")
_kpis = require_permission("reports.kpis", "R")
# The dashboard shows the figures to anyone who can read it; opening one up needs reports.kpis.
_board = require_any_permission(("branch-console.dashboard", "R"), ("reports.kpis", "R"))


def _period(period: str | None, from_: str | None, to: str | None, default: str = "last30"):
    try:
        return branch_analytics_service.resolve_period(period, from_, to, default)
    except branch_analytics_service.AnalysisError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)


async def _answer(call):
    try:
        return await call
    except branch_analytics_service.AnalysisError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)


@router.get("/analysis/options")
async def analysis_options(user: User = Depends(_analysis)) -> dict:
    """What the ABC and XYZ filters offer: periods, departments, categories, brands and suppliers."""
    return await branch_analytics_service.options()


@router.get("/analysis/abc")
async def analysis_abc(
    period: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None,
    basis: str = Query("sales", pattern="^(sales|profit|units)$"), department: str | None = None, category: str | None = None,
    brand: str | None = None, supplierId: str | None = None, abcClass: str | None = Query(None, pattern="^[ABCabc]$"),
    xyzClass: str | None = None, cell: str | None = None, q: str | None = None,
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), user: User = Depends(_analysis),
) -> dict:
    """Items ranked by sales value, gross profit or units, with their A/B/C and X/Y/Z classes, a page at a time."""
    p = _period(period, from_, to, "last6m")
    scope = branch_analytics_service.scope_from(department, category, brand, supplierId)
    return await _answer(branch_analytics_service.abc(p, basis, scope, abcClass, xyzClass, cell, q, limit, offset))


@router.get("/analysis/xyz")
async def analysis_xyz(
    period: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None,
    basis: str = Query("sales", pattern="^(sales|profit|units)$"), department: str | None = None, category: str | None = None,
    brand: str | None = None, supplierId: str | None = None, xyzClass: str | None = None, q: str | None = None,
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), user: User = Depends(_analysis),
) -> dict:
    """Items by how steadily they sell, steadiest first."""
    p = _period(period, from_, to, "last6m")
    scope = branch_analytics_service.scope_from(department, category, brand, supplierId)
    return await _answer(branch_analytics_service.xyz(p, basis, scope, xyzClass, q, limit, offset))


@router.get("/analysis/matrix")
async def analysis_matrix(
    period: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None,
    basis: str = Query("sales", pattern="^(sales|profit|units)$"), department: str | None = None, category: str | None = None,
    brand: str | None = None, supplierId: str | None = None, user: User = Depends(_analysis),
) -> dict:
    """The nine boxes AX to CZ (and Too new to tell): Items, share of sales and what to do about each."""
    p = _period(period, from_, to, "last6m")
    scope = branch_analytics_service.scope_from(department, category, brand, supplierId)
    return await _answer(branch_analytics_service.matrix(p, basis, scope))


@router.get("/analysis/sold-least")
async def analysis_sold_least(
    period: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None,
    department: str | None = None, category: str | None = None, brand: str | None = None, supplierId: str | None = None,
    q: str | None = None, sort: str = Query("units", pattern="^(units|stockValue|cover)$"), unsoldOnly: bool = False,
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), user: User = Depends(_analysis),
) -> dict:
    """Every Item on the shelf or sold in the period, least sold first, including stocked Items that didn't sell."""
    p = _period(period, from_, to, "last6m")
    scope = branch_analytics_service.scope_from(department, category, brand, supplierId)
    return await _answer(branch_analytics_service.sold_least(p, scope, q, sort, limit, offset, unsoldOnly))


@router.get("/analysis/items/{product_id}")
async def analysis_item(
    product_id: str, period: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None,
    user: User = Depends(_analysis),
) -> dict:
    """One Item over the period: its sales in each day, week or month, its classes, who sold it, which days, its bills."""
    p = _period(period, from_, to, "last6m")
    return await _answer(branch_kpi_service.analysis_item(p, product_id))


@router.get("/kpis")
async def kpi_board(
    period: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None, user: User = Depends(_board),
) -> dict:
    """The branch figures for a period: sales, profit, bills, returns, discounts, payments, hours, tills, the mix,
    staff and stock, each with the period before to compare."""
    return await _answer(branch_kpi_service.board(_period(period, from_, to, "today")))


@router.get("/kpis/{kpi_id}")
async def kpi_detail(
    kpi_id: str, period: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None,
    root: str | None = None, day: str | None = None, productId: str | None = None, userId: str | None = None,
    groupKind: str | None = None, group: str | None = None, invoice: str | None = None, path: str | None = None,
    user: User = Depends(_kpis),
) -> dict:
    """One figure opened up, narrowed by what the reader picked on the way down: a day, a department, category or
    brand, an Item, a person, or a single bill. `path` is the order they picked them in, for the breadcrumb."""
    p = _period(period, from_, to, "today")
    return await _answer(branch_kpi_service.detail(kpi_id, p, root, day, productId, userId, groupKind, group, invoice, path, user))
