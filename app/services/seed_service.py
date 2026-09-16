"""Day-one seed: exactly the accounts in frontend-baseline.md §1.1, with RoleDefaultPermission
templates materialized into each seeded user's real UserPermission rows (contracts.md §2.3),
plus the reference/seed data I3's Store domain needs (frontend-baseline.md §2.2/§2.3/§2.8).
Idempotent — a no-op if roles already exist, so it's safe to run on every startup.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.core.abilities import BRANCH_MANAGER, LEGACY_JOBS, PRESETS, SALESPERSON, legacy_grants, preset_grants
from app.core.resources import ROLE_TEMPLATES, excluded_resources_for_role, resources_for_role
from app.core.security import hash_password
from app.models import (
    Batch,
    GiftVoucher,
    Location,
    Party,
    PaymentMethod,
    Product,
    Role,
    RoleDefaultPermission,
    StockMovement,
    Supplier,
    Transfer,
    TransferLine,
    User,
    UserPermission,
    VoucherRedemption,
)

PRODUCTS = [
    ("p-1", "166247", "Coca Cola Bottle 1.5L", "150", "17", False, "bottle", "8901234567890", "carton", 12),
    ("p-2", "166301", "Eggs (Dozen)", "320", "0", False, "dozen", "2200000000018", "tray", 30),
    ("p-3", "100261", "Provas Duo Tablet 30s", "300", "0", False, "box", None, "case", 10),
    ("p-4", "200555", "Bananas (Loose)", "180", "0", True, "kg", None, None, None),
    ("p-5", "150210", "Lay's Chips 40g", "60", "17", False, "pack", "6291041500210", "box", 24),
    ("p-6", "150900", "Nestle Milk Pack 1L", "260", "0", False, "pack", "6291041509003", "crate", 12),
    ("p-7", "180044", "Panadol Extra 10s", "45", "0", False, "strip", None, "box", 10),
    ("p-8", "210087", "Tomatoes (Loose)", "90", "0", True, "kg", None, None, None),
    ("p-9", "190033", "Lifebuoy Soap 100g", "75", "17", False, "pc", "8901030517129", "carton", 48),
    ("p-10", "220019", "Basmati Rice 1kg", "320", "0", True, "kg", None, None, None),
]

PAYMENT_METHODS = [
    ("CASH", "Cash", "cash"),
    ("CARD", "Card", "card"),
    ("EASYPAISA", "Easypaisa", "wallet"),
    ("JAZZCASH", "JazzCash", "wallet"),
    ("BANK", "Bank transfer", "bank"),
    ("CREDIT", "Credit", "credit"),
    ("VOUCHER", "Gift Voucher", "gift-voucher"),
    ("POINTS", "Loyalty points", "points"),
]


async def ensure_payment_methods() -> None:
    """Payment methods added after a branch was first set up (Bank transfer, Loyalty points) reach it on
    its next start; existing ones keep whatever name they have."""
    for code, name, kind in PAYMENT_METHODS:
        if not await PaymentMethod.exists(code=code):
            await PaymentMethod.create(code=code, name=name, kind=kind)

LOCATIONS = [
    ("loc-1", "Main Store", "floor", 1),
    ("loc-2", "Back Room", "back-room", 2),
    ("loc-3", "Fridge", "cold", 1),
]

SUPPLIERS = [
    ("sup-1", "SUP786", "786 Traders (FINO)", "Fino Representative", "061-2116302"),
    ("sup-2", "SUPGRO", "Grocers United Distribution", "Waqas Ahmed", "0300-9988776"),
]

# (product_id, location_id, qty, days_ago) — frontend-baseline.md §2.7's exact 10 opening movements.
OPENING_MOVEMENTS = [
    ("p-1", "loc-1", "240", 5),
    ("p-2", "loc-1", "90", 4),
    ("p-3", "loc-1", "120", 6),
    ("p-4", "loc-1", "42", 1),
    ("p-5", "loc-1", "480", 5),
    ("p-6", "loc-3", "60", 2),
    ("p-7", "loc-1", "100", 7),
    ("p-8", "loc-1", "35", 1),
    ("p-9", "loc-2", "144", 8),
    ("p-10", "loc-2", "80", 3),
]

# (product_id, lot_number, days_until_expiry, received_qty)
SEED_BATCHES = [
    ("p-3", "PV-2408", 20, "120"),
    ("p-1", "CC-2411", 180, "240"),
    ("p-2", None, 11, "90"),
    ("p-6", "NM-0912", 5, "60"),
]

# The two starting points for a new person. Nothing else is a role: what anyone can do is ticked on their own
# account (core/abilities.py). The id stays "cashier" — every permission row and the head office copy use it.
ROLES = [
    ("cashier", "Salesperson", "/store/billing"),
    ("branch-manager", "Branch Manager", "/branch-console/dashboard"),
]
# The fixed roles there used to be; `revise_legacy_roles` moves their people onto per-person access.
LEGACY_ROLES = ("sales-manager", "stock-keeper", "inventory-manager")

USERS = [
    ("cashier@branch.dmarina.pk", "cashier123", "cashier", "Salesperson"),
    ("branchmanager@branch.dmarina.pk", "branch123", "branch-manager", "Branch Manager"),
]


async def seed_if_empty() -> None:
    if await Role.exists():
        return

    for id_, sku, name, price, tax_rate, is_weighed, unit, barcode, pack_unit, pack_size in PRODUCTS:
        await Product.create(
            id=id_, sku=sku, name=name, price=Decimal(price), tax_rate=Decimal(tax_rate),
            is_weighed=is_weighed, unit=unit, barcode=barcode, pack_unit=pack_unit, pack_size=pack_size,
        )

    for code, name, kind in PAYMENT_METHODS:
        await PaymentMethod.create(code=code, name=name, kind=kind)

    for id_, name, kind, priority in LOCATIONS:
        await Location.create(id=id_, name=name, kind=kind, priority=priority)

    for id_, code, name, contact, phone in SUPPLIERS:
        await Supplier.create(id=id_, code=code, name=name, contact_person=contact, phone=phone)

    now = datetime.now(timezone.utc)
    for product_id, location_id, qty, days_ago in OPENING_MOVEMENTS:
        await StockMovement.create(
            product_id=product_id, location_id=location_id, kind="receive", qty=Decimal(qty),
            origin_user=None, at=now - timedelta(days=days_ago),
        )
    for product_id, lot_number, days_until_expiry, received_qty in SEED_BATCHES:
        await Batch.create(
            product_id=product_id, lot_number=lot_number,
            expiry=now + timedelta(days=days_until_expiry), received_qty=Decimal(received_qty),
        )

    t1 = await Transfer.create(
        from_warehouse="Central Godown", status="in_transit", vehicle="LEB-4471", driver="Nadeem",
        requested_at=now - timedelta(days=1), dispatched_at=now, dispute_open=False,
    )
    await TransferLine.create(transfer=t1, product_id="p-1", qty_sent=Decimal("120"), qty_received=None)

    t2 = await Transfer.create(
        from_warehouse="Central Godown", status="received_short", vehicle="LEB-2290", driver="Kashif",
        requested_at=now - timedelta(days=4), dispatched_at=now - timedelta(days=3),
        received_at=now - timedelta(days=2), dispute_open=True,
        dispute_note="Received 96 of 100. 4 boxes missing.",
    )
    await TransferLine.create(transfer=t2, product_id="p-3", qty_sent=Decimal("100"), qty_received=Decimal("96"))

    await Party.create(
        id="00000000-0000-0000-0000-000000000000", code="CASH", name="Walk-in Party", is_walk_in=True,
        due_days=0, credit_allowed=False, credit_limit=Decimal("0"), credit_balance=Decimal("0"),
        tier="retail", active=True,
    )
    await Party.create(
        code="ALI001", name="Ali Traders", is_walk_in=False, phone="0300-1234567", loyalty_no="LY-000142",
        cnic="36302-1234567-8", address="Shop 14, Garden Town", area="Garden Town", contact_person="Ali Raza",
        due_days=30, credit_allowed=True, credit_limit=Decimal("500000"), credit_balance=Decimal("120000"),
        tier="wholesale", active=True,
    )
    await Party.create(
        code="SANA02", name="Sana Bibi", is_walk_in=False, phone="0321-1234567", loyalty_no="LY-000201",
        due_days=0, credit_allowed=False, credit_limit=Decimal("0"), credit_balance=Decimal("0"),
        tier="retail", active=True,
    )

    gv1 = await GiftVoucher.create(
        code="GV-84213", face_value=Decimal("2000"), balance=Decimal("2000"), issued_to_name=None,
        issued_at=now - timedelta(days=10), expires_at=now + timedelta(days=170), status="active",
    )
    gv2 = await GiftVoucher.create(
        code="GV-71190", face_value=Decimal("5000"), balance=Decimal("1500"), issued_to_name="Ali Traders",
        issued_at=now - timedelta(days=27), expires_at=now + timedelta(days=153), status="active",
    )
    await VoucherRedemption.create(voucher=gv2, invoice_number="HO-2026-000110", amount=Decimal("3500"))

    for role_id, name, landing in ROLES:
        role = await Role.create(id=role_id, name=name, landing=landing)
        for resource, actions in preset_grants(role_id).items():
            await RoleDefaultPermission.create(role=role, resource=resource, can_read="R" in actions, can_write="W" in actions, can_execute="X" in actions)

    for email, password, role_id, name in USERS:
        user = await User.create(
            name=name, email=email.lower(), password_hash=hash_password(password), role_id=role_id,
            title=PRESETS[role_id]["label"], discount_limit=PRESETS[role_id]["discountLimit"],
        )
        templates = await RoleDefaultPermission.filter(role_id=role_id)
        for template in templates:
            await UserPermission.create(
                user=user,
                resource=template.resource,
                can_read=template.can_read,
                can_write=template.can_write,
                can_execute=template.can_execute,
                granted_by=None,
            )


# Seeded placeholder names that were never a real person — safe to re-label when the role is
# renamed. A name a Branch Manager actually typed is never touched, which is why this is an exact
# match against the old placeholders rather than a search for "Cashier" anywhere in a name.
_RENAMED_PLACEHOLDERS = {
    "Cashier": "Salesperson",
    "Cashier 2": "Salesperson 2",
    "Cashier 3": "Salesperson 3",
}


async def revise_legacy_roles() -> int:
    """Move everyone on a retired fixed role (Sales Manager, Stock Keeper, Inventory Manager) — and anyone
    on a starting point who has no title yet — onto per-person access: their job becomes their title, their
    access becomes that job's ticks, and they get the job's discount limit. Runs at every startup and finds
    nothing to do once it has run; the retired roles are removed when nobody is left on them."""
    from app.models import Counter
    from app.services import staff_sync_service

    # The first start on per-person access also gives every untitled account its job's clean ticks; after
    # that only a retired role (arriving from an old head office copy, say) is moved, so a title left blank
    # by a Branch Manager never resets that person's access.
    first_run = not await Counter.exists(id="per-person-access")
    moved = 0
    for user in await User.all():
        legacy = user.role_id in LEGACY_ROLES
        untitled = first_run and user.title is None and user.role_id in LEGACY_JOBS
        if not (legacy or untitled):
            continue
        job = LEGACY_JOBS[user.role_id]
        grants = legacy_grants(user.role_id)
        await UserPermission.filter(user=user).delete()
        for resource, actions in grants.items():
            await UserPermission.create(
                user=user, resource=resource, can_read="R" in actions, can_write="W" in actions, can_execute="X" in actions,
            )
        # A seeded placeholder name is the old role's name; a real person's name is never touched.
        user.title = job["title"]
        user.role_id = job["role"]
        user.discount_limit = job["limit"]
        await user.save(update_fields=["title", "role_id", "discount_limit", "updated_at"])
        await staff_sync_service.emit(user.id)
        moved += 1
    if first_run:
        await Counter.create(id="per-person-access", value=1)
    for role_id in LEGACY_ROLES:
        if await Role.exists(id=role_id) and not await User.exists(role_id=role_id):
            await RoleDefaultPermission.filter(role_id=role_id).delete()
            await Role.filter(id=role_id).delete()
    for role_id, _name, _landing in ROLES:
        role = await Role.get_or_none(id=role_id)
        if role and not role.managed_by_head_office:
            # The starting point's own list, with the exact ticks — not "everything on every resource".
            await RoleDefaultPermission.filter(role=role).delete()
            for resource, actions in preset_grants(role_id).items():
                await RoleDefaultPermission.create(role=role, resource=resource, can_read="R" in actions, can_write="W" in actions, can_execute="X" in actions)
    return moved


async def sync_role_labels() -> None:
    """Re-apply the role display names and landing pages from ROLES on every startup.

    `seed_if_empty` only ever runs on a fresh database, so without this a rename would reach new
    installs and never reach the branch that is actually trading. Renaming a role is a label
    change, not a migration — the role *id* is untouched, so every permission row, template and
    account still points exactly where it did.
    """
    for role_id, name, landing in ROLES:
        role = await Role.get_or_none(id=role_id)
        if role and (role.name != name or role.landing != landing):
            role.name, role.landing = name, landing
            await role.save(update_fields=["name", "landing"])
            print(f"  role '{role_id}' is now shown as '{name}'", flush=True)

    for old, new in _RENAMED_PLACEHOLDERS.items():
        renamed = await User.filter(name=old).update(name=new)
        if renamed:
            print(f"  renamed {renamed} seeded account(s): {old} -> {new}", flush=True)


async def sync_role_resource_grants() -> None:
    """Backfills any resource added to `core/resources.py` (and therefore to a role's template)
    onto every existing user of that role. Without this, a resource added after a user was
    seeded would never reach them — RoleDefaultPermission templates only materialize into real
    UserPermission rows at User.create() time, per contracts.md's own documented limitation.
    Runs on every startup.

    Additive for ordinary grants, and only for resources new to the template since the last startup —
    never re-adds a permission a Branch Manager or head office deliberately removed. The one thing it *does* remove is a resource the role
    template explicitly excludes (`resources.py`'s `exclude`), because that's a standing policy
    ("a Cashier must never hold discount-override authority"), not a per-user customization
    someone might legitimately have made."""
    from app.services import staff_sync_service

    # Only resources new to a role's template since the last startup go out. Re-adding the whole
    # template every time quietly undid every access a Branch Manager — or head office — took away.
    new_for_role: dict[str, set[str]] = {}
    for role in await Role.all():
        template = resources_for_role(role.id)
        if role.managed_by_head_office:
            # Head office's list is this role's standard access; the software's defaults don't apply.
            new_for_role[role.id] = set()
        elif role.rolled_out_resources is None:
            # First startup that keeps track: every earlier startup already handed out today's template.
            new_for_role[role.id] = set()
        else:
            new_for_role[role.id] = template - set(role.rolled_out_resources)
        if role.rolled_out_resources != sorted(template):
            role.rolled_out_resources = sorted(template)
            await role.save(update_fields=["rolled_out_resources"])

    for user in await User.all():
        changed = False
        added = new_for_role.get(user.role_id, set())
        if added:
            granted = set(await UserPermission.filter(user=user).values_list("resource", flat=True))
            exact = preset_grants(user.role_id)
            for resource in added - granted:
                actions = exact.get(resource, {"R", "W", "X"})
                await UserPermission.create(
                    user=user, resource=resource, can_read="R" in actions, can_write="W" in actions, can_execute="X" in actions, granted_by=None,
                )
                changed = True
        excluded = excluded_resources_for_role(user.role_id)
        if excluded:
            removed = await UserPermission.filter(user=user, resource__in=list(excluded)).delete()
            changed = changed or bool(removed)
        if changed:
            await staff_sync_service.emit(user.id)

    # The role's own template rows feed every *future* user created into that role, so a stale
    # row here would silently re-grant an excluded resource to the next hire.
    for role_id in ROLE_TEMPLATES:
        excluded = excluded_resources_for_role(role_id)
        if excluded:
            await RoleDefaultPermission.filter(role_id=role_id, resource__in=list(excluded)).delete()


async def give_branch_managers_the_books() -> int:
    """Once: every Branch Manager gets the accounts abilities, so the books have someone until an accountant is added.
    After that it's the Branch Manager's to hand out or take away like any other tick."""
    from app.core.abilities import ACCOUNTS_ABILITIES
    from app.models import Counter
    from app.services import staff_sync_service

    if await Counter.exists(id="rollout:accounts-branch-managers"):
        return 0
    wanted: dict[str, set[str]] = {}
    for resource, action in ACCOUNTS_ABILITIES:
        wanted.setdefault(resource, set()).add(action)
    for actions in wanted.values():
        if actions & {"W", "X"}:
            actions.add("R")
    changed = 0
    for user in await User.filter(role_id="branch-manager"):
        touched = False
        for resource, actions in wanted.items():
            perm = await UserPermission.get_or_none(user=user, resource=resource)
            if perm is None:
                await UserPermission.create(user=user, resource=resource, can_read="R" in actions, can_write="W" in actions,
                                            can_execute="X" in actions, granted_by=None)
                touched = True
            else:
                before = (perm.can_read, perm.can_write, perm.can_execute)
                perm.can_read = perm.can_read or "R" in actions
                perm.can_write = perm.can_write or "W" in actions
                perm.can_execute = perm.can_execute or "X" in actions
                if (perm.can_read, perm.can_write, perm.can_execute) != before:
                    await perm.save()
                    touched = True
        if touched:
            changed += 1
            await staff_sync_service.emit(user.id, None)
    await Counter.create(id="rollout:accounts-branch-managers", value=1)
    return changed


async def give_managers_the_counter_board() -> int:
    """Once: every Branch Manager gets the new ability to put people on counters.

    The startup backfill above only notices resources that are new; `store.counters` was already
    there as a read, so the write action that came with the Counter Board would never have reached
    anybody. After this it is a tick like any other.
    """
    from app.models import Counter
    from app.services import staff_sync_service

    if await Counter.exists(id="rollout:counter-board"):
        return 0
    changed = 0
    for user in await User.filter(role_id="branch-manager"):
        perm = await UserPermission.get_or_none(user=user, resource="store.counters")
        if perm is None:
            await UserPermission.create(user=user, resource="store.counters", can_read=True, can_write=True,
                                        can_execute=False, granted_by=None)
        elif not perm.can_write:
            perm.can_read = True
            perm.can_write = True
            await perm.save()
        else:
            continue
        changed += 1
        await staff_sync_service.emit(user.id, None)
    await Counter.create(id="rollout:counter-board", value=1)
    return changed

