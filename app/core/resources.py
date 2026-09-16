"""Fixed resource catalog for the Branch Server — mirrors frontend-baseline.md §2.1's route table.

Not user-editable data. Adding a new screen/action means adding a line here, not a migration.
"""

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
    # Seeing how this branch is connected to head office, and pushing now rather than waiting for
    # the next scheduled tick. Under `branch-console` so the Branch Manager picks it up from the
    # existing prefix — the person who gets asked "is our data reaching head office?" is the
    # person standing in the branch, and they should be able to answer without ringing anyone.
    "branch-console.sync",
    "reports",
    # Backing the database up (Backup Now, the daily backup, downloads) and putting a backup back. Restore is a
    # Branch Manager's alone — see core/abilities.py.
    "branch-console.backup",
    "branch-console.backup.restore",
    # The books. Seeing them; making vouchers and recording cheques; posting and reversing; the chart of accounts;
    # taking payments from credit customers; closing months.
    "accounts.books",
    "accounts.vouchers",
    "accounts.vouchers.post",
    "accounts.chart",
    "accounts.receivables",
    "accounts.period",
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
