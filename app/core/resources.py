"""Fixed resource catalog for the Branch Server — mirrors frontend-baseline.md §2.1's route table.

Not user-editable data. Adding a new screen/action means adding a line here, not a migration.
"""

ACCOUNTS_RESOURCES: list[str] = [
    "accounts.desk",
    "accounts.trial-balance",
    "accounts.income-statement",
    "accounts.balance-sheet",
    "accounts.month-by-month",
    "accounts.day-book",
    "accounts.ledger",
    "accounts.vouchers",
    "accounts.vouchers.post",
    "accounts.vouchers.reverse",
    "accounts.opening-balances",
    "accounts.chart",
    "accounts.receivables",
    "accounts.payables",
    "accounts.cheques",
    "accounts.fixed-assets",
    "accounts.tax",
    "accounts.settings",
    "accounts.period",
]

# Every account belongs to exactly one area (services/accounts_areas.py decides which).
AREA_KEYS: list[str] = [
    "cash-bank", "receivables", "stock", "advances", "tax", "fixed-assets", "inter-office",
    "payables", "customer-balances", "other-liabilities", "equity", "income", "cost-of-sales", "expenses",
]
AREA_RESOURCES: list[str] = [f"accounts.area.{key}" for key in AREA_KEYS]

RESOURCES: list[str] = [
    "store.billing",
    "store.discount-override",
    "store.till",
    "store.xz",
    "store.hold-recall",
    "store.returns",
    "store.gift-vouchers",
    "store.counters",
    "store.staff-on-duty",
    # Printing a bill made earlier again, marked REPRINT with who and when (recorded in the activity log).
    "store.reprint",
    "inventory.overview",
    "inventory.catalog",
    "inventory.suppliers",
    "inventory.receiving",
    "inventory.purchase-orders",
    "inventory.purchase-orders.approve",
    "inventory.purchase-returns",
    "inventory.movements",
    "inventory.batches",
    "inventory.labels",
    "inventory.counts",
    "inventory.counts.approve",
    "inventory.adjustments",
    "inventory.adjustments.approve",
    "inventory.transfers",
    # Holding an incoming shipment back from being received (damaged, wrong goods) until it's released.
    "inventory.transfers.hold",
    # Sending stock to another branch; without .approve the send waits for someone who has it.
    "inventory.transfers.send",
    "inventory.transfers.approve",
    # Asking head office for stock: drafting, sending and withdrawing a request. The shipment it becomes is received as usual.
    "inventory.requests",
    "inventory.locations",
    "branch-console.dashboard",
    "branch-console.approvals",
    "branch-console.staff",
    "branch-console.staff-access",
    "branch-console.customers",
    # D.Marina members and their points: finding, adding, correcting a member.
    "branch-console.members",
    # How points are earned and what they're worth. Head office can set the same rules.
    "branch-console.loyalty",
    # The branch's lists and settings: Item lists (departments, brands, units, GST rates, customer groups), reasons,
    # payment methods, and what bills print with the gift voucher rules.
    "branch-console.item-lists",
    "branch-console.reasons",
    "branch-console.payment-methods",
    "branch-console.shop-settings",
    # Seeing how this branch is connected to head office, and pushing now rather than waiting for
    # the next scheduled tick. Under `branch-console` so the Branch Manager picks it up from the
    # existing prefix — the person who gets asked "is our data reaching head office?" is the
    # person standing in the branch, and they should be able to answer without ringing anyone.
    "branch-console.sync",
    "reports",
    # ABC / XYZ analysis of Items, and dashboard figures opened down to the bill.
    "reports.analysis",
    "reports.kpis",
    # Backing the database up (Backup Now, the daily backup, downloads) and putting a backup back. Restore is a
    # Branch Manager's alone — see core/abilities.py.
    "branch-console.backup",
    "branch-console.backup.restore",
    # The books, a screen or action each. Whole-book reports always show the whole book; the ledger, the chart and
    # vouchers only show accounts in the areas below. See core/abilities.py and services/accounts_areas.py.
    *ACCOUNTS_RESOURCES,
    # Which accounts a person sees (R) and can put on a voucher line (W), one resource per area.
    *AREA_RESOURCES,
]

# Resource that gates the delegated permission-management endpoints themselves (contracts.md §2.6).
RBAC_MANAGEMENT_RESOURCE = "branch-console.staff-access"


def resources_for_role(role_id: str) -> set[str]:
    """The resources a starting point (Salesperson, Branch Manager) grants. See core/abilities.py."""
    from app.core.abilities import preset_grants

    return set(preset_grants(role_id))


def excluded_resources_for_role(role_id: str) -> set[str]:
    """What an account can never hold because of how it started. Everything else is per person; staff and
    access belong to Branch Manager accounts only, so they are stripped from anyone else — at startup, and
    from whatever head office or a request tries to give."""
    from app.core.abilities import BRANCH_MANAGER, MANAGER_ONLY_RESOURCES

    return set() if role_id == BRANCH_MANAGER else set(MANAGER_ONLY_RESOURCES)


# The starting points a new person can be given.
ROLE_TEMPLATES: dict[str, dict] = {"cashier": {}, "branch-manager": {}}
