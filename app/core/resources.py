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
    "inventory.purchase-returns",
    "inventory.movements",
    "inventory.batches",
    "inventory.labels",
    "inventory.counts",
    "inventory.counts.approve",
    "inventory.adjustments",
    "inventory.adjustments.approve",
    "inventory.transfers",
    "branch-console.dashboard",
    "branch-console.approvals",
    "branch-console.staff",
    "branch-console.staff-access",
    "branch-console.customers",
    # Seeing how this branch is connected to head office, and pushing now rather than waiting for
    # the next scheduled tick. Under `branch-console` so the Branch Manager picks it up from the
    # existing prefix — the person who gets asked "is our data reaching head office?" is the
    # person standing in the branch, and they should be able to answer without ringing anyone.
    "branch-console.sync",
    "reports",
]

# Resource that gates the delegated permission-management endpoints themselves (contracts.md §2.6).
RBAC_MANAGEMENT_RESOURCE = "branch-console.staff-access"


def _matches(resource: str, prefix: str) -> bool:
    return resource == prefix or resource.startswith(prefix + ".")


# role slug -> module prefixes it holds today (frontend-baseline.md §1.1), plus any explicit extra
# grant, minus any resource the role must never hold even though a prefix would otherwise cover it.
#
# `exclude` is a policy statement, not a preference: a Cashier inherits all of `store.*`, but
# `store.discount-override` is the one authority the whole above-authority-discount flow depends on
# NOT being self-serve — if the cashier ringing the sale can approve it, the F9 manager sign-in is
# theatre. Sales Manager is the approver; Cashier is the one who needs approving.
ROLE_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "cashier": {"prefixes": ["store"], "extra": [], "exclude": ["store.discount-override"]},
    "sales-manager": {"prefixes": ["store"], "extra": [], "exclude": []},
    "stock-keeper": {"prefixes": ["inventory"], "extra": [], "exclude": []},
    "inventory-manager": {"prefixes": ["inventory", "reports"], "extra": [], "exclude": []},
    "branch-manager": {"prefixes": ["branch-console", "reports"], "extra": [], "exclude": []},
}

_EMPTY_TEMPLATE: dict[str, list[str]] = {"prefixes": [], "extra": [], "exclude": []}


def resources_for_role(role_id: str) -> set[str]:
    template = ROLE_TEMPLATES.get(role_id, _EMPTY_TEMPLATE)
    granted = {r for r in RESOURCES if any(_matches(r, p) for p in template["prefixes"])}
    granted.update(template.get("extra", []))
    granted.difference_update(template.get("exclude", []))
    return granted


def excluded_resources_for_role(role_id: str) -> set[str]:
    """Resources this role must never hold — enforced on every startup, unlike ordinary grants
    which a Branch Manager is free to customize per user."""
    return set(ROLE_TEMPLATES.get(role_id, _EMPTY_TEMPLATE).get("exclude", []))
