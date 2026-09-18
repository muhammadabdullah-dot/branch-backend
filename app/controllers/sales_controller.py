from datetime import datetime

from fastapi import HTTPException, status

from app.models import PaymentMethod, SaleRecord, User
from app.schemas.sales import (
    DiscountApprovalOut,
    DiscountApprovalRequest,
    NextInvoiceNumberOut,
    ReceiptReprintOut,
    SaleCreateRequest,
    SaleLineOut,
    SaleListOut,
    SaleRecordOut,
    SaleSlipOut,
    SaleTenderOut,
)
from app.services import discount_approval_service, media_service, pharmacy_service, sales_service
from app.schemas.sales import PaymentProofOut


async def _sale_out(sale: SaleRecord, viewer: User | None = None, departments: set[str] | None = None) -> SaleRecordOut:
    """`viewer` is who the bill is shown to. Someone who doesn't sell Pharmacy Items gets its Pharmacy Items as one line,
    "Pharmacy slip P-0042 · 3 items · Rs 743", never by name (services/pharmacy_service.py). `departments` saves reading
    the pharmacy setting again for each bill of a list."""
    method_names = {m.code: m.name for m in await PaymentMethod.all()}
    lines = [
        SaleLineOut(
            productId=str(l.product_id), name=l.product.name, sku=l.product.sku,
            qty=l.qty, unitPrice=l.unit_price, isWeighed=l.product.is_weighed, isReturn=l.is_return,
            discAmount=l.disc_amount, aliasCode=l.alias_code,
            level=l.sell_level, levelQty=l.level_qty, levelPrice=l.level_price, levelDetail=l.level_detail,
            returnOf=l.return_of_invoice,
        )
        for l in sale.lines
    ]
    if pharmacy_service.hides_pharmacy(viewer):
        lines = pharmacy_service.fold_sale_lines(sale, lines, departments if departments is not None else await pharmacy_service.departments())
    return SaleRecordOut(
        id=str(sale.id), invoiceNumber=sale.invoice_number, at=sale.at,
        cashierId=str(sale.cashier_id), partyId=str(sale.party_id), partyName=sale.party.name,
        lines=lines,
        gross=sale.gross, discTotal=sale.disc_total, fare=sale.fare, gst=sale.gst,
        grandTotal=sale.grand_total, netValue=sale.net_value,
        discountOverrideBy=(sale.discount_override_by.name if sale.discount_override_by else None),
        earnedPoints=sale.earned_points,
        memberCode=sale.member.code if sale.member else None,
        memberName=sale.member.name if sale.member else None,
        pointsRedeemed=sale.points_redeemed,
        memberPoints=sale.member.points_balance if sale.member else None,
        tenders=[
            SaleTenderOut(
                code=t.code, name=method_names.get(t.code, t.code), amount=t.amount, reference=t.reference,
                transactionId=t.transaction_id, account=t.account, hasProof=bool(t.proof),
            )
            for t in sale.tenders
        ],
        received=sale.received, cashBack=sale.cash_back, isCreditSale=sale.is_credit_sale,
        fbrInvoiceNumber=sale.fbr_invoice_number,
        slips=[SaleSlipOut(**s) for s in (sale.slips or []) if isinstance(s, dict) and s.get("number")],
    )


async def next_invoice_number() -> NextInvoiceNumberOut:
    return NextInvoiceNumberOut(invoiceNumber=await sales_service.peek_next_invoice_number())


async def request_discount_approval(user: User, payload: DiscountApprovalRequest) -> DiscountApprovalOut:
    try:
        approval = await discount_approval_service.issue(
            user, payload.billId, payload.maxPercent, payload.email, payload.password
        )
    except discount_approval_service.ApprovalError as exc:
        raise HTTPException(exc.status, exc.message)
    return DiscountApprovalOut(
        token=approval.token, approverId=str(approval.approver.id), approverName=approval.approver.name,
        maxPercent=approval.max_percent, expiresAt=approval.expires_at,
        expiresInSeconds=int((approval.expires_at - datetime.now(approval.expires_at.tzinfo)).total_seconds()),
    )


async def create(user: User, payload: SaleCreateRequest) -> SaleRecordOut:
    try:
        sale = await sales_service.create_sale(user, payload)
    except sales_service.SaleError as exc:
        # 409 for a bill that would sell at a loss; 400 for everything else wrong with it.
        raise HTTPException(exc.status, exc.message)
    return await _sale_out(sale, user)


async def upload_proof(content: bytes) -> PaymentProofOut:
    try:
        return PaymentProofOut(proofId=sales_service.save_payment_proof(content))
    except media_service.MediaError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)


async def proof_file(invoice_number: str, code: str):
    from fastapi.responses import FileResponse

    found = await sales_service.payment_proof(invoice_number, code)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No screenshot for that payment")
    path, content_type = found
    return FileResponse(path, media_type=content_type, headers={"Cache-Control": "private, max-age=3600"})


async def get_by_invoice(invoice_number: str, user: User | None = None) -> SaleRecordOut:
    sale = await sales_service.find_by_invoice(invoice_number)
    if not sale:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sale not found")
    return await _sale_out(sale, user)


async def list_sales(from_at: datetime | None, to_at: datetime | None, limit: int, offset: int, user: User | None = None) -> SaleListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    sales, total = await sales_service.list_sales(from_at, to_at, limit, offset)
    departments = await pharmacy_service.departments()
    return SaleListOut(items=[await _sale_out(s, user, departments) for s in sales], total=total)


REPRINT_ROUTE = "/sales/{invoice_number}/reprint"


async def reprint(invoice_number: str, user: User) -> ReceiptReprintOut:
    """A bill made earlier, to print again. The POST that prints it is recorded in the activity log like every other
    action (middlewares/activity.py), so the copy number counts the reprints recorded before this one."""
    from datetime import timezone

    from app.models import ActivityLog, TillSession

    sale = await sales_service.find_by_invoice(invoice_number)
    if not sale:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"There is no bill {invoice_number.strip().upper()} at this branch.")
    earlier = await ActivityLog.filter(
        route=REPRINT_ROUTE, method="POST", status_code__lt=400, path__iexact=f"/sales/{sale.invoice_number}/reprint",
    ).count()
    till_label = None
    if sale.till_session_id:
        session = await TillSession.get_or_none(id=sale.till_session_id).prefetch_related("counter")
        if session:
            till_label = f"{session.counter.name if session.counter else 'Till'} · {session.session_number}"
    out = await _sale_out(sale, user)
    # The member's points today aren't what they were on the bill's day, so a reprint leaves the balance off.
    out.memberPoints = None
    return ReceiptReprintOut(
        sale=out, cashierName=sale.cashier.name if sale.cashier else None, tillLabel=till_label,
        reprintedAt=datetime.now(timezone.utc), reprintedBy=user.name, copyNumber=earlier + 1,
    )
