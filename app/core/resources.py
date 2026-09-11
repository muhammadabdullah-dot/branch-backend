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
    "inventory.receiving",
    "inventory.movements",
    "inventory.batches",
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
    "reports",
]

# Resource that gates the delegated permission-management endpoints themselves (contracts.md §2.6).
RBAC_MANAGEMENT_RESOURCE = "branch-console.staff-access"


def _matches(resource: str, prefix: str) -> bool:
    return resource == prefix or resource.startswith(prefix + ".")


# role slug -> module prefixes it holds today (frontend-baseline.md §1.1), plus any explicit extra grant.
ROLE_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "cashier": {"prefixes": ["store"], "extra": []},
    "sales-manager": {"prefixes": ["store"], "extra": []},
    "stock-keeper": {"prefixes": ["inventory"], "extra": []},
    "inventory-manager": {"prefixes": ["inventory", "reports"], "extra": []},
    "branch-manager": {"prefixes": ["branch-console", "reports"], "extra": []},
}


def resources_for_role(role_id: str) -> set[str]:
    template = ROLE_TEMPLATES.get(role_id, {"prefixes": [], "extra": []})
    granted = {r for r in RESOURCES if any(_matches(r, p) for p in template["prefixes"])}
    granted.update(template["extra"])
    return granted
