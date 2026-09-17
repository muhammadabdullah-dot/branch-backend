"""What a person at the branch can do, in the words a Branch Manager uses.

Access is per person, not per role. Every tick on the access screen is one ability here, and each ability
is exactly one permission underneath (a resource and R, W or X), so what the screen shows and what the
server enforces can never drift apart. Changing something (W) or deciding something (X) always brings
seeing it (R) along.

There are two starting points — Salesperson and Branch Manager (everything) — and nothing else is fixed:
a Sales Manager is a salesperson with discount approval and reports ticked, an Inventory Manager is stock
work with its approvals ticked. How much discount a person may give is set per person, beside these.

The books are a tick per screen and action, plus which accounts a person sees and uses, by area (cash and bank,
tax, expenses…). An area limits the ledger, the chart and vouchers; whole-book reports are ticks of their own and
always show the whole book.

One thing is not a tick: deciding what other people may do. Only a Branch Manager account can open the
staff screen, add people or change anyone's access, so it can't be handed to anyone else.
"""
from decimal import Decimal

# (group, resource, action, label, hint)
ABILITIES: list[tuple[str, str, str, str, str]] = [
    # ── the counter ──
    ("counter", "store.billing", "R", "Open Billing", "See the till screen and look Items up."),
    ("counter", "store.billing", "W", "Ring up sales and take payment", "Includes giving discount up to their own limit."),
    ("counter", "store.discount-override", "X", "Approve discounts for others", "Only up to this person's own discount limit."),
    ("counter", "store.hold-recall", "W", "Hold and recall bills", ""),
    ("counter", "store.returns", "W", "Take returns and give refunds", ""),
    ("counter", "store.till", "W", "Open, cash in / out and close the till", ""),
    ("counter", "store.xz", "R", "X and Z reports", "The till's day figures."),
    ("counter", "store.gift-vouchers", "R", "See gift vouchers", ""),
    ("counter", "store.gift-vouchers", "W", "Take gift vouchers as payment", ""),
    ("counter", "store.gift-vouchers", "X", "Issue gift vouchers", ""),
    ("counter", "store.counters", "R", "Counter board", "Which counters are open, who is on each and what they've taken."),
    ("counter", "store.counters", "W", "Put people on counters", "Assign and change who works which counter, and add counters."),
    ("counter", "store.staff-on-duty", "R", "Staff on duty", "Who is on the floor now, since when, and what they've rung."),
    ("counter", "store.reprint", "X", "Reprint a receipt", "Print a bill made earlier again. It prints marked REPRINT with who and when."),
    # ── stock ──
    ("stock", "inventory.overview", "R", "Stock overview", ""),
    ("stock", "inventory.catalog", "R", "See Items and prices", ""),
    ("stock", "inventory.catalog", "W", "Add and edit Items and prices", ""),
    ("stock", "inventory.suppliers", "W", "Add and edit suppliers", ""),
    ("stock", "inventory.locations", "W", "Add and edit shop locations", ""),
    ("stock", "inventory.purchase-orders", "W", "Raise purchase orders", ""),
    ("stock", "inventory.purchase-orders.approve", "X", "Approve purchase orders", ""),
    ("stock", "inventory.receiving", "W", "Receive supplier goods (GRN)", ""),
    ("stock", "inventory.purchase-returns", "W", "Return goods to suppliers", ""),
    ("stock", "inventory.counts", "W", "Count stock", ""),
    ("stock", "inventory.counts.approve", "X", "Approve stock counts", "Nobody approves their own count."),
    ("stock", "inventory.adjustments", "W", "Adjust stock (damage, expiry, loss)", ""),
    ("stock", "inventory.adjustments.approve", "X", "Approve stock adjustments", "Nobody approves their own adjustment."),
    ("stock", "inventory.movements", "R", "Stock movements", ""),
    ("stock", "inventory.batches", "R", "Batches and expiry", ""),
    ("stock", "inventory.labels", "R", "Print barcode labels", ""),
    # ── shipments ──
    ("shipments", "inventory.transfers", "R", "See shipments", "Stock coming in and going out."),
    ("shipments", "inventory.transfers", "W", "Receive incoming shipments", "Count what arrived into a location."),
    ("shipments", "inventory.transfers.hold", "X", "Hold an incoming shipment", "Stop it being received (damaged, wrong goods) until released."),
    ("shipments", "inventory.transfers.send", "W", "Send stock to another branch", "Without approval rights it waits for someone who has them."),
    ("shipments", "inventory.transfers.approve", "X", "Approve outgoing shipments", "Sends are dispatched straight away for this person."),
    ("shipments", "inventory.requests", "R", "See stock requests", "What this branch asked head office for, and what head office decided."),
    ("shipments", "inventory.requests", "W", "Ask head office for stock", "Draft, send and withdraw requests."),
    # ── customers ──
    ("customers", "branch-console.customers", "R", "See customers and parties", ""),
    ("customers", "branch-console.customers", "W", "Add and edit customers and parties", ""),
    ("customers", "branch-console.members", "R", "See D.Marina members", ""),
    ("customers", "branch-console.members", "W", "Add and edit members", ""),
    ("customers", "branch-console.loyalty", "W", "Change the loyalty points rules", ""),
    # ── managing the branch ──
    ("branch", "branch-console.dashboard", "R", "Branch dashboard", ""),
    ("branch", "branch-console.approvals", "R", "Approvals inbox", "Counts and adjustments waiting for a decision."),
    ("branch", "reports", "R", "Reports", "Sales, profit and stock reports."),
    ("branch", "reports.analysis", "R", "ABC and XYZ analysis", "Which Items bring the money in and which sell steadily."),
    ("branch", "reports.kpis", "R", "Figures you can open up", "Every dashboard figure down to the day, the Item, the person and the bill."),
    ("branch", "branch-console.sync", "W", "Head office sync", "See the link to head office and send now."),
    ("branch", "branch-console.backup", "W", "Back up the database", "Backup Now to a chosen folder, the daily backup, and downloading a backup."),
    # ── lists and settings ──
    ("lists", "branch-console.item-lists", "R", "See Item lists", "Departments, categories, classes, brands, manufacturers, units, GST rates and customer groups."),
    ("lists", "branch-console.item-lists", "W", "Change Item lists", "Add, rename, merge and switch off. A rename changes every Item or customer using it."),
    ("lists", "branch-console.reasons", "R", "See reasons", "Reasons for stock adjustments, returns to suppliers and customer returns."),
    ("lists", "branch-console.reasons", "W", "Change reasons", "Add, reword and switch off reasons."),
    ("lists", "branch-console.payment-methods", "R", "See payment methods", "Which payment methods this branch takes."),
    ("lists", "branch-console.payment-methods", "W", "Change payment methods", "Rename a method or switch it off at this branch."),
    ("lists", "branch-console.shop-settings", "R", "See receipt and gift voucher settings", ""),
    ("lists", "branch-console.shop-settings", "W", "Change receipt and gift voucher settings", "What bills print, gift voucher value and validity, the wholesale discount and the usual low stock level."),
    # ── accounts: the books. Whole-book reports always show every account, because half a statement misleads. ──
    ("accounts-books", "accounts.desk", "R", "Accounts Desk", "Money on hand, what's owed both ways and how the month is going."),
    ("accounts-books", "accounts.desk", "X", "Post the records now", "Posts the latest sales, receipts and payments to the books without waiting."),
    ("accounts-books", "accounts.trial-balance", "R", "Trial Balance", "Always the whole book."),
    ("accounts-books", "accounts.income-statement", "R", "Income Statement", "Always the whole book."),
    ("accounts-books", "accounts.balance-sheet", "R", "Balance Sheet", "Always the whole book."),
    ("accounts-books", "accounts.month-by-month", "R", "Month by Month", "Always the whole book."),
    ("accounts-books", "accounts.day-book", "R", "Day Book", "Every posted voucher in full, whatever accounts it uses."),
    ("accounts-books", "accounts.ledger", "R", "Account Ledger", "Only for the accounts they can see, ticked at the bottom."),
    ("accounts-books", "accounts.receivables", "R", "Receivables", "What each credit customer owes, and for how long."),
    ("accounts-books", "accounts.payables", "R", "Payables", "What each supplier is owed, and for how long."),
    ("accounts-books", "accounts.tax", "R", "Tax reports", "GST and withholding tax."),
    # ── accounts: vouchers and money ──
    ("accounts-vouchers", "accounts.vouchers", "R", "See vouchers", "Only vouchers whose every account they can see."),
    ("accounts-vouchers", "accounts.vouchers", "W", "Write and change draft vouchers", "Every line must be an account they can use. Drafts can be cancelled too."),
    ("accounts-vouchers", "accounts.vouchers.post", "X", "Post vouchers", "A posted voucher is in the books; after that it can only be reversed."),
    ("accounts-vouchers", "accounts.vouchers.reverse", "X", "Reverse posted vouchers", "A journal that undoes the voucher line for line. Both stay on the record."),
    ("accounts-vouchers", "accounts.opening-balances", "R", "See the opening balances", ""),
    ("accounts-vouchers", "accounts.opening-balances", "W", "Write the opening balances", "Including filling them in from the branch's records."),
    ("accounts-vouchers", "accounts.receivables", "W", "Take payments from credit customers", "Cash goes into the till; card, bank and wallet payments are recorded too."),
    ("accounts-vouchers", "accounts.receivables", "X", "Void a customer payment", ""),
    ("accounts-vouchers", "accounts.cheques", "R", "See cheques", "Cheques received and when they are due."),
    ("accounts-vouchers", "accounts.cheques", "W", "Record, clear, bounce and cancel cheques", ""),
    ("accounts-vouchers", "accounts.fixed-assets", "R", "Fixed asset register", "Furniture, equipment and vehicles, and what they are worth now."),
    ("accounts-vouchers", "accounts.fixed-assets", "W", "Add, change and dispose of fixed assets", ""),
    ("accounts-vouchers", "accounts.fixed-assets", "X", "Prepare the depreciation run", ""),
    # ── accounts: setting the books up and closing months ──
    ("accounts-setup", "accounts.chart", "R", "Chart of Accounts", "Only the accounts they can see."),
    ("accounts-setup", "accounts.chart", "W", "Change the chart of accounts", "Add account groups, expense heads and bank accounts."),
    ("accounts-setup", "accounts.settings", "R", "Books Settings", "When the books start, the financial year and where each payment lands."),
    ("accounts-setup", "accounts.settings", "W", "Change the books settings", "Moving the books' start or where a payment lands posts everything again."),
    ("accounts-setup", "accounts.period", "X", "Close and reopen months", "Also posting everything again. Nothing dated in a closed month can change."),
]

# The areas every account falls into (services/accounts_areas.py): (key, label, what's in it).
AREAS: list[tuple[str, str, str]] = [
    ("cash-bank", "Cash, bank and wallets", "Cash in hand, petty cash, bank accounts, card and wallet settlements."),
    ("receivables", "What customers owe", "Each credit customer's own account."),
    ("stock", "Stock", "Stock in trade and stock in transit."),
    ("advances", "Advances, deposits and other assets", "Advances to suppliers and staff, security deposits, prepaid costs."),
    ("tax", "Tax (GST and withholding)", "GST input and output, advance income tax, withholding tax."),
    ("fixed-assets", "Fixed assets and depreciation", "Furniture, equipment, vehicles and their depreciation."),
    ("inter-office", "Head office and branch accounts", "What the branch and head office owe each other."),
    ("payables", "What is owed to suppliers", "Each supplier's own account."),
    ("customer-balances", "Gift vouchers and points owed", "Gift vouchers not spent yet, and loyalty points."),
    ("other-liabilities", "Accrued costs and loans", "Salaries and bills payable, the suspense account, loans."),
    ("equity", "Capital and equity", "Owner's capital, drawings and retained earnings."),
    ("income", "Sales and other income", "Sales, returns, discounts and other income."),
    ("cost-of-sales", "Cost of sales and stock losses", "Cost of goods sold, shortages and stock written off."),
    ("expenses", "Expenses", "Salaries, rent, bills and every other running cost."),
]
AREA_LABELS = {key: label for key, label, _ in AREAS}
# One See and one Use tick per area, shown on the access screen as a grid rather than a list.
AREAS_GROUP = "accounts-areas"
AREA_COLUMNS = [("R", "See"), ("W", "Use")]
for _key, _label, _hint in AREAS:
    ABILITIES.append((AREAS_GROUP, f"accounts.area.{_key}", "R", f"{_label} (see)", _hint))
    ABILITIES.append((AREAS_GROUP, f"accounts.area.{_key}", "W", f"{_label} (use on vouchers)", _hint))

GROUPS: list[tuple[str, str]] = [
    ("counter", "Sales counter"),
    ("stock", "Stock"),
    ("shipments", "Shipments"),
    ("customers", "Customers & members"),
    ("branch", "Running the branch"),
    ("lists", "Lists and settings"),
    ("accounts-books", "Accounts: books and reports"),
    ("accounts-vouchers", "Accounts: vouchers and money"),
    ("accounts-setup", "Accounts: chart, settings and month end"),
    (AREAS_GROUP, "Accounts: which accounts they can see and use"),
]

SALESPERSON = "cashier"
BRANCH_MANAGER = "branch-manager"

ACCOUNTS_ABILITIES = {(resource, action) for group, resource, action, _, _ in ABILITIES if group.startswith("accounts")}

# How the six accounts ticks there used to be become today's, so nobody gains or loses anything when the books were
# split into a tick per screen and per area. (old resource, action) -> what it now stands for.
_BOOKS_SCREENS = ("accounts.trial-balance", "accounts.income-statement", "accounts.balance-sheet", "accounts.month-by-month",
                  "accounts.day-book", "accounts.ledger", "accounts.vouchers", "accounts.opening-balances", "accounts.chart",
                  "accounts.receivables", "accounts.payables", "accounts.cheques", "accounts.fixed-assets", "accounts.tax",
                  "accounts.settings")
_AREA_RESOURCES = tuple(f"accounts.area.{key}" for key, _, _ in AREAS)
LEGACY_ACCOUNTS: dict[tuple[str, str], set[tuple[str, str]]] = {
    ("accounts.books", "R"): {("accounts.desk", "R"), ("accounts.desk", "X")} | {(r, "R") for r in _BOOKS_SCREENS + _AREA_RESOURCES},
    ("accounts.vouchers", "W"): {(r, "W") for r in ("accounts.vouchers", "accounts.opening-balances", "accounts.cheques", "accounts.fixed-assets") + _AREA_RESOURCES},
    ("accounts.vouchers.post", "X"): {(r, "X") for r in ("accounts.vouchers.post", "accounts.vouchers.reverse", "accounts.receivables", "accounts.fixed-assets")},
    ("accounts.chart", "W"): {("accounts.chart", "W")},
    ("accounts.period", "X"): {("accounts.period", "X"), ("accounts.settings", "W")},
    ("accounts.receivables", "R"): {("accounts.receivables", "R")},
    ("accounts.receivables", "W"): {("accounts.receivables", "W")},
    ("reports", "R"): {("reports.analysis", "R"), ("reports.kpis", "R")},
}
# Gone for good; everything else keeps its name.
RETIRED_RESOURCES = frozenset({"accounts.books"})


def is_legacy(resources) -> bool:
    """Access written before the books were split still holds "See the books"."""
    return bool(RETIRED_RESOURCES & set(resources))


def translate_legacy(grants: dict[str, set[str]]) -> dict[str, set[str]]:
    """Old accounts ticks (and the old Reports tick) read as the ticks they now stand for, merged with what's there.
    Used once by the rollout, and on access arriving from a head office that hasn't caught up (`is_legacy`): never on
    today's access, where it would hand back a tick somebody took away."""
    out = {resource: set(actions) for resource, actions in grants.items()}
    for (resource, action), targets in LEGACY_ACCOUNTS.items():
        if action in grants.get(resource, set()):
            for target, target_action in targets:
                out.setdefault(target, set()).add(target_action)
    for resource in RETIRED_RESOURCES:
        out.pop(resource, None)
    return normalise(out)


def translate_legacy_resources(resources: set[str]) -> set[str]:
    """The same for a role's standard access, which is a list of resources given in full."""
    full = translate_legacy({resource: {"R", "W", "X"} for resource in resources})
    return set(full)

# Staff and access: a Branch Manager's by being one, never something ticked on someone else.
# Restoring a backup replaces everything since it was taken, so it stays with Branch Manager accounts too.
MANAGER_ONLY_RESOURCES = frozenset({"branch-console.staff", "branch-console.staff-access", "branch-console.backup.restore"})
MANAGER_ONLY_GRANTS = {("branch-console.staff", "R"), ("branch-console.staff-access", "R"), ("branch-console.staff-access", "W"),
                       ("branch-console.backup.restore", "R"), ("branch-console.backup.restore", "X")}

# The two starting points. Everything after that is ticks on the person.
PRESETS: dict[str, dict] = {
    SALESPERSON: {
        "label": "Salesperson",
        "discountLimit": Decimal("5"),
        "abilities": {
            ("store.billing", "R"), ("store.billing", "W"), ("store.hold-recall", "W"), ("store.returns", "W"),
            ("store.till", "W"), ("store.xz", "R"), ("store.gift-vouchers", "W"), ("store.gift-vouchers", "X"),
            ("store.counters", "R"), ("store.staff-on-duty", "R"),
        },
    },
    BRANCH_MANAGER: {
        "label": "Branch Manager",
        "discountLimit": Decimal("100"),
        "abilities": {(resource, action) for _, resource, action, _, _ in ABILITIES} | MANAGER_ONLY_GRANTS,
    },
}

# What a person with no limit set may give: the starting point their account came from.
DEFAULT_DISCOUNT_LIMIT = Decimal("5")


def preset_limit(role_id: str) -> Decimal:
    return PRESETS.get(role_id, {}).get("discountLimit", DEFAULT_DISCOUNT_LIMIT)


def normalise(grants: dict[str, set[str]]) -> dict[str, set[str]]:
    """Changing or deciding something brings seeing it along."""
    out = {resource: set(actions) for resource, actions in grants.items() if actions}
    for actions in out.values():
        if actions & {"W", "X"}:
            actions.add("R")
    return out


def preset_grants(role_id: str) -> dict[str, set[str]]:
    grants: dict[str, set[str]] = {}
    for resource, action in PRESETS.get(role_id, {}).get("abilities", set()):
        grants.setdefault(resource, set()).add(action)
    return normalise(grants)


# The jobs the old fixed roles stood for, as ticks — used once to move existing accounts onto per-person
# access. The job name stays on the account as its title.
_STOCK_WORK = {
    ("inventory.overview", "R"), ("inventory.catalog", "W"), ("inventory.suppliers", "W"), ("inventory.locations", "W"),
    ("inventory.purchase-orders", "W"), ("inventory.receiving", "W"), ("inventory.purchase-returns", "W"),
    ("inventory.counts", "W"), ("inventory.adjustments", "W"), ("inventory.movements", "R"), ("inventory.batches", "R"),
    ("inventory.labels", "R"), ("inventory.transfers", "W"), ("inventory.transfers.send", "W"), ("inventory.requests", "W"),
}
LEGACY_JOBS: dict[str, dict] = {
    "cashier": {"title": "Salesperson", "role": SALESPERSON, "limit": Decimal("5"), "abilities": PRESETS[SALESPERSON]["abilities"]},
    "sales-manager": {
        "title": "Sales Manager", "role": SALESPERSON, "limit": Decimal("20"),
        "abilities": PRESETS[SALESPERSON]["abilities"] | {("store.discount-override", "X"), ("reports", "R"), ("reports.analysis", "R"),
                                                          ("reports.kpis", "R"), ("branch-console.members", "R")},
    },
    "stock-keeper": {"title": "Stock Keeper", "role": SALESPERSON, "limit": Decimal("0"), "abilities": _STOCK_WORK},
    "inventory-manager": {
        "title": "Inventory Manager", "role": SALESPERSON, "limit": Decimal("0"),
        "abilities": _STOCK_WORK | {
            ("inventory.purchase-orders.approve", "X"), ("inventory.counts.approve", "X"), ("inventory.adjustments.approve", "X"),
            ("inventory.transfers.approve", "X"), ("inventory.transfers.hold", "X"), ("branch-console.approvals", "R"), ("reports", "R"),
            ("reports.analysis", "R"), ("reports.kpis", "R"),
        },
    },
    "branch-manager": {"title": "Branch Manager", "role": BRANCH_MANAGER, "limit": Decimal("100"), "abilities": PRESETS[BRANCH_MANAGER]["abilities"]},
}


def legacy_grants(old_role: str) -> dict[str, set[str]]:
    grants: dict[str, set[str]] = {}
    for resource, action in LEGACY_JOBS[old_role]["abilities"]:
        grants.setdefault(resource, set()).add(action)
    return normalise(grants)
