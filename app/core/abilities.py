"""What a person at the branch can do, in the words a Branch Manager uses.

Access is per person, not per role. Every tick on the access screen is one ability here, and each ability
is exactly one permission underneath (a resource and R, W or X), so what the screen shows and what the
server enforces can never drift apart. Changing something (W) or deciding something (X) always brings
seeing it (R) along.

There are two starting points — Salesperson and Branch Manager (everything) — and nothing else is fixed:
a Sales Manager is a salesperson with discount approval and reports ticked, an Inventory Manager is stock
work with its approvals ticked. How much discount a person may give is set per person, beside these.

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
    ("branch", "branch-console.sync", "W", "Head office sync", "See the link to head office and send now."),
    ("branch", "branch-console.backup", "W", "Back up the database", "Backup Now to a chosen folder, the daily backup, and downloading a backup."),
    # ── accounts ──
    ("accounts", "accounts.books", "R", "See the books", "Ledgers, trial balance, income statement, balance sheet, receivables and payables."),
    ("accounts", "accounts.vouchers", "W", "Make vouchers and record cheques", "Cash, bank and journal vouchers are saved as drafts."),
    ("accounts", "accounts.vouchers.post", "X", "Post and reverse vouchers", "A posted voucher is in the books; after that it can only be reversed."),
    ("accounts", "accounts.chart", "W", "Change the chart of accounts", "Add account groups, expense heads and bank accounts."),
    ("accounts", "accounts.receivables", "W", "Take payments from credit customers", "Cash goes into the till; card, bank and wallet payments are recorded too."),
    ("accounts", "accounts.period", "X", "Close months and set when the books start", "Nothing dated in a closed month can change after that."),
]

GROUPS: list[tuple[str, str]] = [
    ("counter", "Sales counter"),
    ("stock", "Stock"),
    ("shipments", "Shipments"),
    ("customers", "Customers & members"),
    ("branch", "Running the branch"),
    ("accounts", "Accounts"),
]

SALESPERSON = "cashier"
BRANCH_MANAGER = "branch-manager"

ACCOUNTS_ABILITIES = {(resource, action) for group, resource, action, _, _ in ABILITIES if group == "accounts"}

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
    ("inventory.labels", "R"), ("inventory.transfers", "W"), ("inventory.transfers.send", "W"),
}
LEGACY_JOBS: dict[str, dict] = {
    "cashier": {"title": "Salesperson", "role": SALESPERSON, "limit": Decimal("5"), "abilities": PRESETS[SALESPERSON]["abilities"]},
    "sales-manager": {
        "title": "Sales Manager", "role": SALESPERSON, "limit": Decimal("20"),
        "abilities": PRESETS[SALESPERSON]["abilities"] | {("store.discount-override", "X"), ("reports", "R"), ("branch-console.members", "R")},
    },
    "stock-keeper": {"title": "Stock Keeper", "role": SALESPERSON, "limit": Decimal("0"), "abilities": _STOCK_WORK},
    "inventory-manager": {
        "title": "Inventory Manager", "role": SALESPERSON, "limit": Decimal("0"),
        "abilities": _STOCK_WORK | {
            ("inventory.purchase-orders.approve", "X"), ("inventory.counts.approve", "X"), ("inventory.adjustments.approve", "X"),
            ("inventory.transfers.approve", "X"), ("inventory.transfers.hold", "X"), ("branch-console.approvals", "R"), ("reports", "R"),
        },
    },
    "branch-manager": {"title": "Branch Manager", "role": BRANCH_MANAGER, "limit": Decimal("100"), "abilities": PRESETS[BRANCH_MANAGER]["abilities"]},
}


def legacy_grants(old_role: str) -> dict[str, set[str]]:
    grants: dict[str, set[str]] = {}
    for resource, action in LEGACY_JOBS[old_role]["abilities"]:
        grants.setdefault(resource, set()).add(action)
    return normalise(grants)
