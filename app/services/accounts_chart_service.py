"""The chart of accounts: the standard D.Marina chart, the accounts automatic vouchers post to, and the
account every credit customer and supplier gets.

Codes follow the levels: type 1 digit, category 2, group 4, sub group 6, account 8 (group + serial), so a
code says where an account sits — 11010001 is an account in group 1101 (CASH IN HAND) of category 11
(CURRENT ASSETS) of type 1 (ASSETS). The standard chart is the same at every branch and at head office,
which is what lets head office add the books together.
"""
import re
from decimal import Decimal

from tortoise.transactions import atomic

from app.core.device_context import get_device_id
from app.models import Account, AccountCategory, AccountGroup, AccountSubGroup, AccountType, OutboxEvent, VoucherLine

TYPES = [
    ("1", "ASSETS", "debit", "balance"),
    ("2", "LIABILITIES", "credit", "balance"),
    ("3", "EQUITY", "credit", "balance"),
    ("4", "REVENUE", "credit", "income"),
    ("5", "EXPENSES", "debit", "income"),
]

CATEGORIES = [
    ("11", "CURRENT ASSETS", "1"), ("12", "NON-CURRENT ASSETS", "1"), ("13", "INTER-OFFICE ACCOUNTS", "1"),
    ("21", "CURRENT LIABILITIES", "2"), ("22", "LONG-TERM LIABILITIES", "2"),
    ("31", "CAPITAL AND RESERVES", "3"),
    ("41", "SALES REVENUE", "4"), ("42", "OTHER INCOME", "4"),
    ("51", "COST OF SALES", "5"), ("52", "OPERATING EXPENSES", "5"), ("53", "FINANCIAL EXPENSES", "5"),
]

GROUPS = [
    ("1101", "CASH IN HAND", "11"), ("1102", "BANK ACCOUNTS", "11"), ("1103", "CARD AND WALLET SETTLEMENTS", "11"),
    ("1104", "TRADE DEBTORS (CUSTOMERS)", "11"), ("1105", "STOCK IN TRADE", "11"),
    ("1106", "ADVANCES, DEPOSITS AND PREPAYMENTS", "11"), ("1107", "TAXES RECEIVABLE", "11"),
    ("1201", "FIXED ASSETS", "12"), ("1202", "ACCUMULATED DEPRECIATION", "12"),
    ("1301", "HEAD OFFICE AND BRANCH CURRENT ACCOUNTS", "13"),
    ("2101", "TRADE CREDITORS (SUPPLIERS)", "21"), ("2102", "TAXES PAYABLE", "21"),
    ("2103", "CUSTOMER BALANCES HELD", "21"), ("2104", "ACCRUED AND OTHER PAYABLES", "21"),
    ("2201", "LOANS AND BORROWINGS", "22"),
    ("3101", "CAPITAL", "31"), ("3102", "RESERVES AND RETAINED EARNINGS", "31"),
    ("4101", "SALES", "41"), ("4102", "SALES RETURNS AND DISCOUNTS", "41"),
    ("4201", "OTHER INCOME", "42"),
    ("5101", "COST OF GOODS SOLD", "51"), ("5102", "STOCK LOSSES AND ADJUSTMENTS", "51"),
    ("5201", "ADMINISTRATIVE EXPENSES", "52"), ("5202", "SALARIES AND STAFF COSTS", "52"), ("5203", "UTILITY BILLS", "52"),
    ("5204", "RENT, REPAIRS AND MAINTENANCE", "52"), ("5205", "FREIGHT AND TRANSPORTATION", "52"), ("5206", "MARKETING EXPENSES", "52"),
    ("5301", "BANK CHARGES AND FINANCE COSTS", "53"),
]

# (code, name, group, kind, system key)
ACCOUNTS = [
    ("11010001", "CASH IN HAND (COUNTER)", "1101", "cash", "cash.counter"),
    ("11010002", "CASH IN HAND (MAIN / SAFE)", "1101", "cash", "cash.main"),
    ("11010003", "PETTY CASH", "1101", "cash", "cash.petty"),
    ("11010004", "CHEQUES IN HAND", "1101", "cash", "cash.cheques"),
    ("11020001", "BANK ACCOUNT (MAIN)", "1102", "bank", "bank.main"),
    ("11030001", "DEBIT / CREDIT CARD PAYMENT", "1103", "wallet", "wallet.card"),
    ("11030002", "EASYPAISA ACCOUNT", "1103", "wallet", "wallet.easypaisa"),
    ("11030003", "JAZZCASH ACCOUNT", "1103", "wallet", "wallet.jazzcash"),
    ("11050001", "STOCK IN TRADE", "1105", "general", "stock.main"),
    ("11050002", "STOCK IN TRANSIT", "1105", "general", "stock.transit"),
    ("11060001", "ADVANCES TO SUPPLIERS", "1106", "general", "advance.suppliers"),
    ("11060002", "STAFF ADVANCES", "1106", "general", None),
    ("11060003", "SECURITY DEPOSITS", "1106", "general", None),
    ("11060004", "PREPAID EXPENSES", "1106", "general", None),
    ("11070001", "SALES TAX (GST) INPUT", "1107", "general", "tax.gst_input"),
    ("11070002", "ADVANCE INCOME TAX", "1107", "general", "tax.advance"),
    ("12010001", "FURNITURE AND FIXTURES", "1201", "general", None),
    ("12010002", "EQUIPMENT AND POS MACHINES", "1201", "general", None),
    ("12010003", "COMPUTERS AND SOFTWARE", "1201", "general", None),
    ("12010004", "VEHICLES", "1201", "general", None),
    ("12010005", "REFRIGERATION AND AIR CONDITIONING", "1201", "general", None),
    ("12020001", "ACCUMULATED DEPRECIATION", "1202", "general", None),
    ("13010001", "HEAD OFFICE CURRENT ACCOUNT", "1301", "interoffice", "interoffice.head_office"),
    ("21020001", "SALES TAX (GST) PAYABLE", "2102", "general", "tax.gst_output"),
    ("21020002", "WITHHOLDING TAX PAYABLE", "2102", "general", "tax.wht_payable"),
    ("21020003", "INCOME TAX PAYABLE", "2102", "general", None),
    ("21030001", "GIFT VOUCHERS OUTSTANDING", "2103", "general", "liab.gift_vouchers"),
    ("21030002", "LOYALTY POINTS PAYABLE", "2103", "general", "liab.loyalty"),
    ("21040001", "SALARIES PAYABLE", "2104", "general", None),
    ("21040002", "UTILITY BILLS PAYABLE", "2104", "general", None),
    ("21040003", "SUSPENSE ACCOUNT", "2104", "general", "suspense"),
    ("22010001", "LOANS AND BORROWINGS", "2201", "general", None),
    ("31010001", "OWNER'S CAPITAL", "3101", "general", "equity.capital"),
    ("31010002", "DRAWINGS", "3101", "general", None),
    ("31020001", "OPENING BALANCE EQUITY", "3102", "general", "equity.opening"),
    ("31020002", "RETAINED EARNINGS", "3102", "general", "equity.retained"),
    ("41010001", "SALES - GENERAL", "4101", "general", "sales.general"),
    ("41020001", "SALES RETURNS", "4102", "general", "sales.returns"),
    ("41020002", "DISCOUNTS ALLOWED", "4102", "general", "sales.discounts"),
    ("42010001", "DELIVERY CHARGES (FARE)", "4201", "general", "income.delivery"),
    ("42010002", "ROUND OFF DIFFERENCES", "4201", "general", "income.rounding"),
    ("42010003", "CASH OVER (TILL EXCESS)", "4201", "general", "income.cash_over"),
    ("42010004", "EXPIRED GIFT VOUCHERS", "4201", "general", "income.voucher_expiry"),
    ("42010005", "STOCK SURPLUS", "4201", "general", "income.stock_gain"),
    ("42010006", "MISCELLANEOUS INCOME", "4201", "general", "income.other"),
    ("42010007", "SUPPLIER DISCOUNTS AND REBATES", "4201", "general", None),
    ("51010001", "COST OF SALES - GENERAL", "5101", "general", "cogs.general"),
    ("51010002", "PURCHASE RETURN PRICE DIFFERENCE", "5101", "general", "cogs.return_difference"),
    ("51020001", "STOCK SHORTAGE (COUNT DIFFERENCES)", "5102", "general", "loss.count"),
    ("51020002", "STOCK WRITTEN OFF - DAMAGE", "5102", "general", "loss.damage"),
    ("51020003", "STOCK WRITTEN OFF - EXPIRY", "5102", "general", "loss.expiry"),
    ("51020004", "TRANSIT LOSSES", "5102", "general", "loss.transit"),
    ("52010001", "CASH SHORT (TILL SHORTAGE)", "5201", "general", "expense.cash_short"),
    ("52010002", "PETTY EXPENSES (UNCLASSIFIED)", "5201", "general", "expense.petty"),
    ("52010003", "PRINTING AND STATIONERY", "5201", "general", None),
    ("52010004", "TEA AND ENTERTAINMENT", "5201", "general", None),
    ("52010005", "OFFICE EXPENSES", "5201", "general", None),
    ("52010006", "CLEANING AND SECURITY", "5201", "general", None),
    ("52010007", "LEGAL AND PROFESSIONAL FEES", "5201", "general", None),
    ("52010008", "DEPRECIATION", "5201", "general", None),
    ("52010009", "TILL RECONCILIATION DIFFERENCES", "5201", "general", "expense.till_reconciliation"),
    ("52020001", "SALARIES AND WAGES", "5202", "general", None),
    ("52020002", "STAFF BONUSES AND INCENTIVES", "5202", "general", None),
    ("52020003", "STAFF FOOD AND WELFARE", "5202", "general", None),
    ("52030001", "ELECTRICITY", "5203", "general", None),
    ("52030002", "GAS", "5203", "general", None),
    ("52030003", "WATER", "5203", "general", None),
    ("52030004", "INTERNET AND TELEPHONE", "5203", "general", None),
    ("52040001", "RENT", "5204", "general", None),
    ("52040002", "REPAIRS AND MAINTENANCE", "5204", "general", None),
    ("52040003", "GENERATOR FUEL", "5204", "general", None),
    ("52050001", "FREIGHT AND CARRIAGE INWARD", "5205", "general", None),
    ("52050002", "DELIVERY VEHICLE FUEL", "5205", "general", None),
    ("52050003", "COURIER AND TRANSPORT", "5205", "general", None),
    ("52060001", "LOYALTY POINTS EXPENSE", "5206", "general", "expense.loyalty"),
    ("52060002", "COMPLIMENTARY GIFT VOUCHERS", "5206", "general", "expense.complimentary_vouchers"),
    ("52060003", "ADVERTISING AND PROMOTIONS", "5206", "general", None),
    ("52060004", "PACKAGING AND SHOPPING BAGS", "5206", "general", None),
    ("53010001", "BANK CHARGES", "5301", "general", "expense.bank_charges"),
    ("53010002", "CARD AND WALLET FEES", "5301", "general", None),
    ("53010003", "MARKUP AND INTEREST", "5301", "general", None),
]

# Which account each payment method's money lands in, unless settings say otherwise.
TENDER_KEYS = {
    "CASH": "cash.counter", "CARD": "wallet.card", "EASYPAISA": "wallet.easypaisa", "JAZZCASH": "wallet.jazzcash",
    "BANK": "bank.main", "VOUCHER": "liab.gift_vouchers", "POINTS": "liab.loyalty",
}

KINDS = ("general", "cash", "bank", "wallet", "customer", "supplier", "interoffice")
CUSTOMERS_GROUP, SUPPLIERS_GROUP, INTEROFFICE_GROUP = "1104", "2101", "1301"
SALES_GROUP, COGS_GROUP = "4101", "5101"


class ChartError(Exception):
    def __init__(self, message: str):
        self.message = message


# ── sync ────────────────────────────────────────────────────────────────────────────────────────

def group_payload(group: AccountGroup) -> dict:
    return {
        "code": group.code, "name": group.name, "categoryCode": group.category_id, "priority": group.priority,
        "manualCode": group.manual_code, "standard": group.standard,
    }


async def account_payload(account: Account) -> dict:
    return {
        "id": str(account.id), "code": account.code, "name": account.name, "groupCode": account.group_id,
        "subGroupCode": account.sub_group_id, "kind": account.kind, "systemKey": account.system_key,
        "partyRef": account.party_ref, "active": account.active, "restricted": account.restricted,
        "bankName": account.bank_name, "bankAccountNo": account.bank_account_no, "standard": account.standard,
    }


async def _emit(kind: str, key: str, payload: dict) -> None:
    await OutboxEvent.create(
        aggregate_type="AccChart", aggregate_id=f"{kind}:{key}", payload={"kind": kind, kind: payload},
        origin_device_id=get_device_id(),
    )


async def emit_group(group: AccountGroup) -> None:
    await _emit("group", group.code, group_payload(group))


async def emit_sub_group(sub: AccountSubGroup) -> None:
    await _emit("subGroup", sub.code, {"code": sub.code, "name": sub.name, "groupCode": sub.group_id, "standard": sub.standard})


async def emit_account(account: Account) -> None:
    await _emit("account", str(account.id), await account_payload(account))


# ── the standard chart ─────────────────────────────────────────────────────────────────────────

async def ensure_standard_chart(include_head_office_account: bool = True) -> int:
    """Create whatever of the standard chart is missing. Never renames or moves anything someone changed.
    Returns how many rows were added."""
    added = 0
    for code, name, nature, statement in TYPES:
        if not await AccountType.exists(code=code):
            await AccountType.create(code=code, name=name, nature=nature, statement=statement)
            added += 1
    for code, name, type_code in CATEGORIES:
        if not await AccountCategory.exists(code=code):
            await AccountCategory.create(code=code, name=name, type_id=type_code)
            added += 1
    for position, (code, name, category) in enumerate(GROUPS):
        if not await AccountGroup.exists(code=code):
            group = await AccountGroup.create(code=code, name=name, category_id=category, priority=position, standard=True)
            await emit_group(group)
            added += 1
        sub_code = f"{code}01"
        if not await AccountSubGroup.exists(code=sub_code):
            sub = await AccountSubGroup.create(code=sub_code, name="DEFAULT SUBGROUP", group_id=code, standard=True)
            await emit_sub_group(sub)
            added += 1
    for code, name, group, kind, key in ACCOUNTS:
        if key == "interoffice.head_office" and not include_head_office_account:
            continue
        if key and await Account.exists(system_key=key):
            continue
        if not key and await Account.exists(code=code):
            continue
        if await Account.exists(code=code):
            code = await next_account_code(group)
        account = await Account.create(
            code=code, name=name, group_id=group, sub_group_id=f"{group}01", kind=kind, system_key=key, standard=True,
        )
        await emit_account(account)
        added += 1
    return added


async def next_account_code(group_code: str) -> str:
    codes = await Account.filter(group_id=group_code).values_list("code", flat=True)
    serials = [int(c[len(group_code):]) for c in codes if c.startswith(group_code) and c[len(group_code):].isdigit()]
    return f"{group_code}{(max(serials) + 1) if serials else 1:04d}"


async def next_group_code(category_code: str) -> str:
    codes = await AccountGroup.filter(category_id=category_code).values_list("code", flat=True)
    serials = [int(c[len(category_code):]) for c in codes if c.startswith(category_code) and c[len(category_code):].isdigit()]
    return f"{category_code}{(max(serials) + 1) if serials else 1:02d}"


async def next_sub_group_code(group_code: str) -> str:
    codes = await AccountSubGroup.filter(group_id=group_code).values_list("code", flat=True)
    serials = [int(c[len(group_code):]) for c in codes if c.startswith(group_code) and c[len(group_code):].isdigit()]
    return f"{group_code}{(max(serials) + 1) if serials else 1:02d}"


# ── the accounts automatic vouchers need ────────────────────────────────────────────────────────

class Resolver:
    """Finds (and where needed makes) the accounts one posting run uses, remembering them for the run."""

    def __init__(self) -> None:
        self._by_key: dict[str, Account] = {}

    async def key(self, key: str) -> Account:
        if key in self._by_key:
            return self._by_key[key]
        account = await Account.get_or_none(system_key=key)
        if account is None:
            if key.startswith("sales.dept.") or key.startswith("cogs.dept."):
                account = await _department_account(key)
            else:
                await ensure_standard_chart()
                account = await Account.get_or_none(system_key=key)
                if account is None:
                    raise ChartError(f"The chart has no account for {key}.")
        self._by_key[key] = account
        return account

    async def department(self, department: str | None, cost: bool = False) -> Account:
        slug = slugify(department)
        if not slug or slug == "general":
            return await self.key("cogs.general" if cost else "sales.general")
        return await self.key(f"{'cogs' if cost else 'sales'}.dept.{slug}")

    async def customer(self, party) -> Account:
        key = f"customer:{party.id}"
        if key not in self._by_key:
            self._by_key[key] = await customer_account(party)
        return self._by_key[key]

    async def supplier(self, supplier) -> Account:
        key = f"supplier:{supplier.id}"
        if key not in self._by_key:
            self._by_key[key] = await supplier_account(supplier)
        return self._by_key[key]

    async def tender(self, code: str, tender_accounts: dict, bank_hint: str | None = None) -> Account:
        chosen = (tender_accounts or {}).get(code)
        if chosen:
            account = await Account.get_or_none(id=chosen, active=True)
            if account:
                return account
        if code == "BANK" and bank_hint:
            hint = bank_hint.strip().lower()
            for account in await Account.filter(kind="bank", active=True):
                if hint in (account.name or "").lower() or hint == (account.bank_account_no or "").lower() or hint in (account.bank_name or "").lower():
                    return account
        key = TENDER_KEYS.get(code)
        return await self.key(key) if key else await self.key("suspense")


def slugify(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")[:40]


async def _department_account(key: str) -> Account:
    cost = key.startswith("cogs.")
    slug = key.split(".", 2)[2]
    group = COGS_GROUP if cost else SALES_GROUP
    from app.models import Product

    names = await Product.exclude(department=None).distinct().values_list("department", flat=True)
    label = next((n for n in names if slugify(n) == slug), slug.replace("-", " "))
    account = await Account.create(
        code=await next_account_code(group), name=f"{'COST OF SALES' if cost else 'SALES'} - {label.upper()}"[:160],
        group_id=group, sub_group_id=f"{group}01", system_key=key, standard=False,
    )
    await emit_account(account)
    return account


async def customer_account(party) -> Account:
    """The customer's own account under TRADE DEBTORS, made the first time it's needed."""
    key = f"customer:{party.id}"
    account = await Account.get_or_none(system_key=key)
    if account is None:
        account = await Account.create(
            code=await next_account_code(CUSTOMERS_GROUP), name=party.name[:160], group_id=CUSTOMERS_GROUP,
            sub_group_id=f"{CUSTOMERS_GROUP}01", kind="customer", system_key=key, party_ref=str(party.id),
            check_limit=bool(party.credit_allowed), balance_limit=party.credit_limit if party.credit_allowed else None,
        )
        await emit_account(account)
    elif account.name != party.name[:160]:
        account.name = party.name[:160]
        await account.save(update_fields=["name", "updated_at"])
        await emit_account(account)
    return account


async def supplier_account(supplier) -> Account:
    key = f"supplier:{supplier.id}"
    account = await Account.get_or_none(system_key=key)
    if account is None:
        account = await Account.create(
            code=await next_account_code(SUPPLIERS_GROUP), name=supplier.name[:160], group_id=SUPPLIERS_GROUP,
            sub_group_id=f"{SUPPLIERS_GROUP}01", kind="supplier", system_key=key, party_ref=str(supplier.id),
        )
        await emit_account(account)
    elif account.name != supplier.name[:160]:
        account.name = supplier.name[:160]
        await account.save(update_fields=["name", "updated_at"])
        await emit_account(account)
    return account


async def ensure_party_accounts() -> int:
    """Every credit customer and every supplier gets its account up front, so they can be picked in vouchers."""
    from app.models import Party, Supplier

    made = 0
    for party in await Party.filter(is_walk_in=False, credit_allowed=True):
        if not await Account.exists(system_key=f"customer:{party.id}"):
            await customer_account(party)
            made += 1
    for supplier in await Supplier.all():
        if not await Account.exists(system_key=f"supplier:{supplier.id}"):
            await supplier_account(supplier)
            made += 1
    return made


# ── reading and changing the chart ─────────────────────────────────────────────────────────────

async def tree() -> dict:
    types = await AccountType.all().order_by("code")
    categories = await AccountCategory.all().order_by("code")
    groups = await AccountGroup.all().order_by("code")
    subs = await AccountSubGroup.all().order_by("code")
    accounts = await Account.all().order_by("code")
    used = {str(a) for a in await VoucherLine.all().distinct().values_list("account_id", flat=True)}
    return {
        "types": [{"code": t.code, "name": t.name, "nature": t.nature, "statement": t.statement} for t in types],
        "categories": [{"code": c.code, "name": c.name, "typeCode": c.type_id} for c in categories],
        "groups": [{**group_payload(g)} for g in groups],
        "subGroups": [{"code": s.code, "name": s.name, "groupCode": s.group_id, "standard": s.standard} for s in subs],
        "accounts": [{**(await account_payload(a)), "checkLimit": a.check_limit, "balanceLimit": a.balance_limit,
                      "manualCode": a.manual_code, "remarks": a.remarks, "used": str(a.id) in used} for a in accounts],
    }


def _clean(value: str | None, limit: int) -> str | None:
    value = (value or "").strip()
    return value[:limit] if value else None


@atomic()
async def create_group(category_code: str, name: str, priority: int = 0, manual_code: str | None = None) -> AccountGroup:
    if not await AccountCategory.exists(code=category_code):
        raise ChartError("Pick the category the group belongs to.")
    name = _clean(name, 100)
    if not name:
        raise ChartError("Give the group a name.")
    if await AccountGroup.filter(category_id=category_code, name__iexact=name).exists():
        raise ChartError(f"There's already a group called {name} in that category.")
    group = await AccountGroup.create(
        code=await next_group_code(category_code), name=name.upper(), category_id=category_code, priority=priority, manual_code=_clean(manual_code, 30),
    )
    sub = await AccountSubGroup.create(code=f"{group.code}01", name="DEFAULT SUBGROUP", group=group)
    await emit_group(group)
    await emit_sub_group(sub)
    return group


@atomic()
async def update_group(code: str, name: str | None, priority: int | None, manual_code: str | None) -> AccountGroup:
    group = await AccountGroup.get_or_none(code=code)
    if not group:
        raise ChartError("That group doesn't exist.")
    if name is not None:
        cleaned = _clean(name, 100)
        if not cleaned:
            raise ChartError("A group needs a name.")
        group.name = cleaned.upper()
    if priority is not None:
        group.priority = priority
    if manual_code is not None:
        group.manual_code = _clean(manual_code, 30)
    await group.save()
    await emit_group(group)
    return group


@atomic()
async def create_sub_group(group_code: str, name: str) -> AccountSubGroup:
    if not await AccountGroup.exists(code=group_code):
        raise ChartError("Pick the group the sub group belongs to.")
    name = _clean(name, 100)
    if not name:
        raise ChartError("Give the sub group a name.")
    sub = await AccountSubGroup.create(code=await next_sub_group_code(group_code), name=name.upper(), group_id=group_code)
    await emit_sub_group(sub)
    return sub


@atomic()
async def update_sub_group(code: str, name: str) -> AccountSubGroup:
    sub = await AccountSubGroup.get_or_none(code=code)
    if not sub:
        raise ChartError("That sub group doesn't exist.")
    cleaned = _clean(name, 100)
    if not cleaned:
        raise ChartError("A sub group needs a name.")
    sub.name = cleaned.upper()
    await sub.save()
    await emit_sub_group(sub)
    return sub


ACCOUNT_FIELDS = ("name", "sub_group_id", "kind", "active", "restricted", "check_limit", "balance_limit", "bank_name", "bank_account_no", "manual_code", "remarks")


async def _type_of_group(group_code: str) -> str:
    group = await AccountGroup.get(code=group_code).prefetch_related("category")
    return group.category.type_id


@atomic()
async def create_account(fields: dict) -> Account:
    group_code = fields.get("group_code")
    if not group_code or not await AccountGroup.exists(code=group_code):
        raise ChartError("Pick the group the account belongs to.")
    name = _clean(fields.get("name"), 160)
    if not name:
        raise ChartError("Give the account a name.")
    kind = fields.get("kind") or "general"
    if kind not in ("general", "cash", "bank", "wallet"):
        raise ChartError("Customer, supplier and branch accounts are made from their own records, not here.")
    if kind in ("cash", "bank", "wallet") and await _type_of_group(group_code) != "1":
        raise ChartError("Cash, bank and wallet accounts belong under an assets group.")
    sub = fields.get("sub_group_code") or f"{group_code}01"
    if not await AccountSubGroup.exists(code=sub, group_id=group_code):
        raise ChartError("That sub group isn't in the chosen group.")
    if await Account.filter(group_id=group_code, name__iexact=name).exists():
        raise ChartError(f"{name} is already an account in that group.")
    account = await Account.create(
        code=await next_account_code(group_code), name=name, group_id=group_code, sub_group_id=sub, kind=kind,
        restricted=bool(fields.get("restricted")), check_limit=bool(fields.get("check_limit")),
        balance_limit=fields.get("balance_limit"), bank_name=_clean(fields.get("bank_name"), 80),
        bank_account_no=_clean(fields.get("bank_account_no"), 40), manual_code=_clean(fields.get("manual_code"), 30),
        remarks=_clean(fields.get("remarks"), 255),
    )
    await emit_account(account)
    return account


@atomic()
async def update_account(account_id: str, fields: dict) -> Account:
    account = await Account.get_or_none(id=account_id)
    if not account:
        raise ChartError("That account doesn't exist.")
    if "group_code" in fields and fields["group_code"] and fields["group_code"] != account.group_id:
        if account.system_key and not account.system_key.startswith(("customer:", "supplier:")):
            raise ChartError("The software posts to this account, so it stays in its group. Rename it if you like.")
        if await _type_of_group(fields["group_code"]) != await _type_of_group(account.group_id):
            raise ChartError("An account can only move to a group of the same type (assets, liabilities, …).")
        account.group_id = fields["group_code"]
        account.sub_group_id = f"{fields['group_code']}01"
    if "name" in fields and fields["name"] is not None:
        cleaned = _clean(fields["name"], 160)
        if not cleaned:
            raise ChartError("An account needs a name.")
        account.name = cleaned
    if "sub_group_code" in fields and fields["sub_group_code"]:
        if not await AccountSubGroup.exists(code=fields["sub_group_code"], group_id=account.group_id):
            raise ChartError("That sub group isn't in the account's group.")
        account.sub_group_id = fields["sub_group_code"]
    if "active" in fields and fields["active"] is not None:
        if not fields["active"] and account.system_key and not account.system_key.startswith(("customer:", "supplier:")):
            raise ChartError("The software posts to this account, so it can't be switched off.")
        account.active = bool(fields["active"])
    for key in ("restricted", "check_limit"):
        if key in fields and fields[key] is not None:
            setattr(account, key, bool(fields[key]))
    if "balance_limit" in fields:
        account.balance_limit = fields["balance_limit"]
    for key, limit in (("bank_name", 80), ("bank_account_no", 40), ("manual_code", 30), ("remarks", 255)):
        if key in fields and fields[key] is not None:
            setattr(account, key, _clean(fields[key], limit))
    if "kind" in fields and fields["kind"] and fields["kind"] != account.kind:
        if account.kind in ("customer", "supplier", "interoffice") or fields["kind"] not in ("general", "cash", "bank", "wallet"):
            raise ChartError("That account's kind is set by what it belongs to.")
        account.kind = fields["kind"]
    await account.save()
    await emit_account(account)
    return account


@atomic()
async def delete_account(account_id: str) -> None:
    account = await Account.get_or_none(id=account_id)
    if not account:
        raise ChartError("That account doesn't exist.")
    if account.system_key:
        raise ChartError("The software uses this account, so it can't be deleted.")
    if await VoucherLine.exists(account_id=account.id):
        raise ChartError("This account has entries in the books. Switch it off instead.")
    await OutboxEvent.create(aggregate_type="AccChart", aggregate_id=f"account:{account.id}",
                             payload={"kind": "accountDeleted", "accountDeleted": {"id": str(account.id)}}, origin_device_id=get_device_id())
    await account.delete()


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))
