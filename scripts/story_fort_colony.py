"""Fort Colony's first seventeen days, 1 to 17 Sep 2026, on a COPY of a branch database, for head office to compare.

    DB_URL=sqlite://<scratch>/fc.db python -m scripts.story_fort_colony --yes --export <scratch>/fc-story.json

Fort Colony has no branch server yet, so head office has never had its figures, and Branch Comparison had only Model Town.
This builds the branch Fort Colony would be if it had traded since 1 Sep, then writes what its branch server would have
sent head office (its figures, its stock list and its books) to one file. `cloud-server/scripts/load_branch_story.py`
lands that file at head office exactly as a sync would.

The copy starts as Model Town's database. Everything Model Town did is cleared; the Item catalog, the company's
suppliers, the item lists, the locations and payment methods stay. Then, dated where each would have happened:

  people      a Branch Manager, a Sales Manager, three salespeople, a stock keeper and an inventory manager of its own
  buying      a first stock of everything it carries on 1 Sep from the suppliers who supply each Item, and top-ups
              on 8 and 14 Sep for what sells faster than it came in
  selling     two counters, a morning and an afternoon drawer, walk-in and wholesale bills, card and wallet buyers
              signed up as members, a few returns, petty cash outs, drawers counted at close (a few rupees out)
  books       head office opening the branch on 1 Sep (cash, bank, fit-out, rent deposit against the two current accounts),
              wholesale customers paying (bank transfers, a cheque cleared), every automatic voucher, suppliers paid
              on their terms, rent, salaries and utilities to 15 Sep, card and wallet takings settled, safe cash banked

What it sells is drawn from Model Town's own September, deliberately different: some of Model Town's best sellers
barely sell here or aren't carried at all, some of its slow Items are Fort Colony's best, and some Items Model Town
never sold do well here. That is what Branch Comparison exists to find.

Real services do the work (receiving, selling, returns, tills, counters, vouchers, posting), so stock, average cost, the
drawer and the books add up exactly as they would from the screens; the script only moves each record back to its
moment. Stock never goes below zero: a bill only takes what is on the shelf. It refuses to run on the live branch.db.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from tortoise import Tortoise

from app.core.config import TORTOISE_ORM, settings

START = date(2026, 9, 1)
END = date(2026, 9, 17)
MID = date(2026, 9, 15)
PKT = timezone(timedelta(hours=5))
ZERO = Decimal("0")
RNG = random.Random(20260917)
NOW = datetime.now(timezone.utc)
KEY = "story:fort-colony-2026-09-17"

FC = {"id": "d1a467e8-dfc5-46ee-ba90-8df5b071e804", "code": "FC", "name": "Fort Colony", "address": "Fort Colony Road",
      "city": "Multan", "phone": "061-2116303"}

STAFF = [
    # name, email, role, title, discount limit
    ("Tariq Mehmood", "tariq.mehmood@fc.dmarina.pk", "branch-manager", "Branch Manager", Decimal("100")),
    ("Faisal Iqbal", "faisal.iqbal@fc.dmarina.pk", "cashier", "Sales Manager", Decimal("20")),
    ("Rabia Anwar", "rabia.anwar@fc.dmarina.pk", "cashier", "Salesperson", Decimal("5")),
    ("Zeeshan Ali", "zeeshan.ali@fc.dmarina.pk", "cashier", "Salesperson", Decimal("5")),
    ("Mariam Javed", "mariam.javed@fc.dmarina.pk", "cashier", "Salesperson", Decimal("5")),
    ("Adeel Aslam", "adeel.aslam@fc.dmarina.pk", "cashier", "Stock Keeper", Decimal("0")),
    ("Kamran Shah", "kamran.shah@fc.dmarina.pk", "cashier", "Inventory Manager", Decimal("0")),
]
TRADE_CUSTOMERS = [("Hassan General Store", Decimal("300000")), ("Noor Kiryana Store", Decimal("150000")), ("Madni Wholesale Mart", Decimal("400000"))]
BUYER_NAMES = ["Sadia Rehman", "Asad Mahmood", "Naila Parveen", "Shahid Latif", "Hira Saeed", "Waqar Younis Khan", "Farah Deeba",
               "Junaid Akram", "Sumaira Aziz", "Imtiaz Hussain", "Rukhsana Kausar", "Omer Farooq", "Nabeela Tariq", "Salman Haider"]
HOUR_WEIGHTS = {9: 3, 10: 6, 11: 8, 12: 6, 13: 4, 14: 3, 15: 4, 16: 6, 17: 9, 18: 11, 19: 10, 20: 6}
DENOMINATIONS = [5000, 1000, 500, 100, 50, 20, 10, 5, 2, 1]
# Wiped from the copy: everything Model Town did.
WIPE = [
    "acc_depreciation_run_lines", "acc_depreciation_runs", "acc_fixed_assets", "acc_voucher_lines", "acc_vouchers", "cheques",
    "customer_payments", "voucher_redemptions", "gift_vouchers", "loyalty_entries", "return_lines", "return_records", "sale_tenders",
    "sale_lines", "sale_records", "held_bills", "cash_movements", "till_sessions", "counter_duties", "sales_counters", "grn_lines",
    "batches", "purchase_return_lines", "purchase_returns", "grns", "purchase_order_lines", "purchase_orders", "physical_counts",
    "adjustments", "transfer_lines", "transfers", "stock_request_lines", "stock_requests", "stock_movements", "product_price_changes",
    "notice_reads", "notices", "activity_logs", "outbox_events", "devices", "members", "user_permissions",
    "acc_accounts", "acc_sub_groups", "acc_groups", "acc_settings",
]
# Model Town's cash outs, from its story: what small spending looks like.
PETTY = [("52010004", (400, 1200), "Tea and biscuits for staff", "Chacha Tea Stall"),
         ("52050003", (300, 900), "Rickshaw to the bank and back", "Rickshaw"),
         ("52040003", (1800, 4200), "Generator diesel during load-shedding", "Shell Chowk Kumharan"),
         ("52010003", (500, 1600), "Receipt rolls and price stickers", "Paper World Stationers")]


def at(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    moment = datetime.combine(day, time(hour, minute, second), tzinfo=PKT).astimezone(timezone.utc)
    return min(moment, NOW - timedelta(minutes=10))


def money(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def say(text: str) -> None:
    print(f"  {text}", flush=True)


def notes_for(amount: Decimal) -> dict[str, int]:
    left, out = int(amount), {}
    for d in DENOMINATIONS:
        if left >= d:
            out[str(d)], left = left // d, left % d
    return out


class FortColony:
    def __init__(self) -> None:
        self.counts: dict[str, int] = defaultdict(int)
        self.stock: dict[str, Decimal] = defaultdict(lambda: ZERO)
        self.ts_columns: list[tuple[str, str]] = []

    # ── moving records to their moment ─────────────────────────────────────────────────────────
    async def load_columns(self) -> None:
        conn = Tortoise.get_connection("default")
        tables = [r["name"] for r in await conn.execute_query_dict("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            if table in ("products", "aerich", "sync_state", "branch_identity"):
                continue
            for col in await conn.execute_query_dict(f"PRAGMA table_info({table})"):
                name, kind = col["name"], (col["type"] or "").upper()
                if kind == "TIMESTAMP" and "expir" not in name and "due" not in name:
                    self.ts_columns.append((table, name))

    async def stamped(self, when: datetime, work):
        """Run `work` (a real service call), then move every record it wrote or touched to `when`."""
        mark = str(datetime.now(timezone.utc) - timedelta(milliseconds=5))
        result = await work()
        conn = Tortoise.get_connection("default")
        value = str(when)
        for table, column in self.ts_columns:
            await conn.execute_query(f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" >= ?', [value, mark])
        return result

    # ── 1. the copy becomes Fort Colony ─────────────────────────────────────────────────────────
    async def model_town_september(self) -> None:
        """What Model Town sold in September, before it's cleared: Fort Colony's demand is drawn from it."""
        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict("""
            SELECT l.product_id AS pid, SUM(CAST(l.qty AS REAL)) AS units, SUM(CAST(l.qty AS REAL) * CAST(l.unit_price AS REAL)) AS value
            FROM sale_lines l JOIN sale_records s ON s.id = l.sale_id
            WHERE s.at >= '2026-08-31 19:00' AND l.is_return = 0 GROUP BY l.product_id""")
        self.mt = {str(r["pid"]): (float(r["units"] or 0), float(r["value"] or 0)) for r in rows}
        say(f"Model Town sold {len(self.mt)} Items in September; Fort Colony's demand is drawn from them")

    async def become_fort_colony(self) -> None:
        from app.models import Counter, Location, Party, PartyContact, Supplier, User, next_value
        from app.services import accounts_chart_service, counter_service, masters_service, rbac_service, vouchers_service
        from app.services.seed_service import ensure_payment_methods

        conn = Tortoise.get_connection("default")
        for table in WIPE:
            await conn.execute_query(f'DELETE FROM "{table}"')
        await Counter.exclude(id__startswith="rollout:").delete()
        await PartyContact.exclude(party_id="00000000-0000-0000-0000-000000000000").delete()
        await Party.filter(is_walk_in=False).delete()
        await Party.filter(is_walk_in=True).update(credit_balance=0)
        await conn.execute_query('DELETE FROM "users"')
        await Supplier.filter(code="OPENING").delete()
        await conn.execute_query(
            'UPDATE "branch_identity" SET branch_id=?, code=?, name=?, address=?, city=?, phone=?, cloud_url=?, sync_secret=?',
            [FC["id"], FC["code"], FC["name"], FC["address"], FC["city"], FC["phone"], "", "not-paired"])
        await conn.execute_query('DELETE FROM "sync_state"')
        await Location.filter(id="loc-1").update(name="Main Store")

        for name, email, role, title, limit in STAFF:
            # A long random password nobody is told: these accounts exist to be named on bills, not to sign in.
            await rbac_service.create_user(name, email, "fc-" + "".join(RNG.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(24)), role,
                                           title=title, discount_limit=limit)
        # Stock staff start as salespeople; their job's usual ticks go on top, as a Branch Manager would give them.
        from app.core.abilities import legacy_grants
        from app.models import UserPermission

        for title, job in (("Stock Keeper", "stock-keeper"), ("Inventory Manager", "inventory-manager"), ("Sales Manager", "sales-manager")):
            for person in await User.filter(title=title):
                for resource, actions in legacy_grants(job).items():
                    row = await UserPermission.get_or_none(user=person, resource=resource)
                    fields = {"can_read": "R" in actions, "can_write": "W" in actions, "can_execute": "X" in actions}
                    if row:
                        await UserPermission.filter(id=row.id).update(**{k: v or getattr(row, k) for k, v in fields.items()})
                    else:
                        await UserPermission.create(user=person, resource=resource, granted_by=None, **fields)
        self.bm = await User.get(title="Branch Manager")
        self.sellers = [u for u in await User.filter(title__in=["Sales Manager", "Salesperson"]).order_by("name")]
        self.stock_staff = [u for u in await User.filter(title__in=["Stock Keeper", "Inventory Manager"])]
        await User.all().update(created_at=at(date(2026, 8, 30), 11))

        await ensure_payment_methods()
        await accounts_chart_service.ensure_standard_chart()
        await accounts_chart_service.ensure_party_accounts()
        row = await vouchers_service.settings()
        row.books_start = START
        row.locked_until = None
        await row.save()
        await vouchers_service.emit_settings(row)

        self.counters = [await counter_service.create_counter(self.bm, "C1", "Counter 1", "Front, by the door"),
                         await counter_service.create_counter(self.bm, "C2", "Counter 2", "Back, by the wholesale shelves")]
        self.trade = []
        for name, limit in TRADE_CUSTOMERS:
            seq = await next_value("party_code", 3)
            party = await Party.create(code=f"FC{seq:04d}", name=name, tier="wholesale", credit_allowed=True, credit_limit=limit, credit_balance=0,
                                       is_walk_in=False, active=True, due_days=15, city="Multan", phone=f"0300{RNG.randrange(1000000, 9999999)}")
            await accounts_chart_service.customer_account(party)
            self.trade.append(party)
        self.walk_in = await Party.get(is_walk_in=True)
        await masters_service.ensure_reasons()
        self.suppliers = {s.code: s for s in await Supplier.filter(active=True)}
        say(f"cleared Model Town's trading; {len(STAFF)} Fort Colony staff, 2 counters, {len(self.trade)} trade customers, {len(self.suppliers)} suppliers")

    # ── 2. what Fort Colony carries and how fast it sells ───────────────────────────────────────
    async def assortment(self) -> None:
        from app.models import Product

        ranked = sorted(self.mt.items(), key=lambda kv: -kv[1][1])
        total = sum(v for _, (_, v) in ranked) or 1
        running, top = 0.0, set()
        for pid, (_, value) in ranked:
            if running / total < 0.8:
                top.add(pid)
            running += value
        ids = [pid for pid, _ in ranked]
        products = {str(p.id): p for p in await Product.filter(id__in=ids, active=True)}
        self.rate: dict[str, float] = {}     # units a day, retail and wholesale together
        self.kind: dict[str, str] = {}
        days_mt = 16.0
        for pid, (units, _value) in ranked:
            p = products.get(pid)
            if not p or not Decimal(p.price or 0) or not Decimal(p.avg_cost or 0):
                continue
            base = units / days_mt
            roll = RNG.random()
            if pid in top and roll < 0.18:
                self.kind[pid] = "not-carried"          # a Model Town best seller Fort Colony doesn't stock
                continue
            if roll < 0.40:
                self.kind[pid], factor = "weak", RNG.uniform(0.0, 0.08)
            elif roll < 0.62 and pid not in top:
                self.kind[pid], factor = "strong", RNG.uniform(3.0, 6.0)
            else:
                self.kind[pid], factor = "similar", RNG.uniform(0.5, 1.2)
            self.rate[pid] = base * factor * 0.8
        # Items Model Town holds but didn't sell in September, which sell well here.
        pool = await Product.filter(active=True, department__in=["NON-FOOD", "KIDS CARE", "PHARMACY", "FASHION", "GROCERY"]).exclude(id__in=ids).limit(4000)
        pool = [p for p in pool if Decimal(p.price or 0) > 50 and Decimal(p.avg_cost or 0) > 0]
        strong_units = sorted(self.rate[pid] for pid, k in self.kind.items() if k == "strong") or [1.0]
        median = strong_units[len(strong_units) // 2]
        for p in RNG.sample(pool, k=min(90, len(pool))):
            self.kind[str(p.id)] = "new-strong"
            self.rate[str(p.id)] = median * RNG.uniform(0.6, 1.6)
        for p in RNG.sample([p for p in pool if str(p.id) not in self.kind], k=min(140, len(pool))):
            self.kind[str(p.id)] = "idle"
            self.rate[str(p.id)] = 0.0
        self.products = {str(p.id): p for p in await Product.filter(id__in=list(self.rate))}
        tally = defaultdict(int)
        for pid in self.rate:
            tally[self.kind[pid]] += 1
        tally["not-carried"] = sum(1 for k in self.kind.values() if k == "not-carried")
        say("assortment: " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))

    # ── 3. buying ───────────────────────────────────────────────────────────────────────────────
    def supplier_for(self, product) -> object:
        from scripts.story_books import supplier_code_for

        code = supplier_code_for(product)
        return self.suppliers.get(code) or self.suppliers.get("SUP786")

    async def deliver(self, day: date, hour: int, wants: dict[str, Decimal]) -> None:
        from app.models import GRN
        from app.schemas.inventory import GRNCreateRequest, GRNLineIn
        from app.services import inventory_service
        from scripts.story_books import pack_for

        by_supplier: dict[str, list] = defaultdict(list)
        for pid, qty in wants.items():
            if qty <= 0:
                continue
            p = self.products[pid]
            pack = pack_for(Decimal(p.price))
            qty = Decimal(max(pack, math.ceil(float(qty) / pack) * pack))
            by_supplier[self.supplier_for(p).code].append((p, qty))
        minute = 5
        for code, lines in sorted(by_supplier.items()):
            supplier = self.suppliers[code]
            receiver = RNG.choice(self.stock_staff)
            request_lines = []
            for p, qty in lines:
                cost = money(Decimal(p.avg_cost) * Decimal(str(RNG.uniform(0.97, 1.02))))
                line = {"productId": str(p.id), "qty": qty, "bonusQty": ZERO, "unitPrice": cost, "discPercent": Decimal(RNG.choice([0, 0, 0, 1, 2])),
                        "misc": ZERO, "taxRate": Decimal(p.tax_rate or 0)}
                if (p.department or "").upper() == "PHARMACY":
                    line["expiry"] = datetime.combine(day + timedelta(days=RNG.randrange(180, 540)), time(0), tzinfo=PKT)
                request_lines.append(GRNLineIn(**line))
            request = GRNCreateRequest(supplierId=supplier.id, locationId="loc-1", gstMode="normal", advanceTax=ZERO,
                                       partyInvNo=f"{code[3:]}-{RNG.randrange(10000, 99999)}", lines=request_lines)
            when = at(day, hour, minute)
            grn = await self.stamped(when, lambda r=request, u=receiver: inventory_service.receive_grn(u, r))
            await GRN.filter(id=grn.id).update(due_date=day + timedelta(days=supplier.due_days or 0))
            for p, qty in lines:
                self.stock[str(p.id)] += qty
            minute += RNG.randrange(12, 35)
            if minute >= 60:
                hour, minute = hour + 1, minute - 60
            self.counts["deliveries from suppliers"] += 1

    async def first_stock(self) -> None:
        wants = {}
        for pid, kind in self.kind.items():
            if pid not in self.rate:
                continue
            need = self.rate[pid] * 17
            if kind == "strong" or kind == "new-strong":
                wants[pid] = Decimal(str(need * RNG.uniform(0.55, 0.8)))
            elif kind == "weak":
                wants[pid] = Decimal(str(max(need * 6, RNG.randrange(12, 60))))
            elif kind == "idle":
                wants[pid] = Decimal(RNG.randrange(6, 36))
            else:
                wants[pid] = Decimal(str(need * RNG.uniform(1.3, 2.2)))
        await self.deliver(START, 9, wants)

    async def top_up(self, day: date) -> None:
        wants = {}
        days_left = (END - day).days + 1
        for pid, rate in self.rate.items():
            if rate <= 0:
                continue
            if float(self.stock[pid]) < rate * 5:
                # Strong sellers are reordered a little short, so they end the fortnight thin on the shelf.
                cover = 0.7 if self.kind[pid] in ("strong", "new-strong") else 1.6
                wants[pid] = Decimal(str(rate * days_left * cover))
        if wants:
            await self.deliver(day, 10, wants)

    # ── 4. selling ──────────────────────────────────────────────────────────────────────────────
    async def open_drawers(self, day: date, cashiers) -> list:
        from app.models import TillSession
        from app.services import till_service

        opened = []
        for counter, cashier, hour in ((self.counters[0], cashiers[0], 9), (self.counters[1], cashiers[1], 12)):
            when = at(day, hour, RNG.randrange(0, 12))
            if when >= NOW - timedelta(minutes=10):
                continue
            till = await self.stamped(when, lambda c=counter, u=cashier: till_service.open_till(u, {"1000": 5}, "", str(c.id)))
            opened.append((till, cashier, hour))
        return opened

    async def close_drawers(self, day: date, opened) -> None:
        from app.services import till_service

        if day >= END:
            return  # today's drawers are still open
        for till, cashier, hour in opened:
            expected = (await till_service.preview_close(cashier, str(till.id)))["netCash"]
            variance = Decimal(RNG.choice([0, 0, 0, 0, -10, -20, 20, -50, 30]))
            counted = notes_for(Decimal(expected) + variance)
            when = at(day, 21, RNG.randrange(15, 40)) if hour == 9 else at(day, 21, RNG.randrange(40, 55))
            await self.stamped(when, lambda u=cashier, t=till, c=counted: till_service.close_till(u, c, str(t.id)))
            self.counts["drawers counted and closed"] += 1

    def pick(self, count: int) -> list[str]:
        pids = [pid for pid, r in self.rate.items() if r > 0 and self.stock[pid] > 0]
        weights = [self.rate[pid] for pid in pids]
        chosen: list[str] = []
        for _ in range(count * 3):
            if len(chosen) >= count or not pids:
                break
            pid = RNG.choices(pids, weights=weights, k=1)[0]
            if pid not in chosen:
                chosen.append(pid)
        return chosen

    def bill_total(self, lines, disc_percent: Decimal) -> Decimal:
        """The bill's net value the way sales_service.create_sale works it out, so card and wallet payments are exact."""
        products = self.products
        item = []
        for l in lines:
            p = products[l.productId]
            amount = l.qty * l.unitPrice * Decimal(p.disc_percent or 0) / 100 + l.qty * Decimal(p.disc_flat or 0)
            item.append(min(amount, l.qty * l.unitPrice))
        after = [l.qty * l.unitPrice - d for l, d in zip(lines, item)]
        discountable = sum((v for l, v in zip(lines, after) if not products[l.productId].lock_disc), ZERO)
        if discountable <= 0:
            disc_percent = ZERO
        line_discs = [d + (ZERO if products[l.productId].lock_disc or discountable == 0 else v * disc_percent / 100) for l, d, v in zip(lines, item, after)]
        gross = sum((l.qty * l.unitPrice for l in lines), ZERO)
        disc_total = sum(item, ZERO) + discountable * disc_percent / 100
        gst = sum(((l.qty * l.unitPrice - ld) * Decimal(products[l.productId].tax_rate or 0) / 100 for l, ld in zip(lines, line_discs)), ZERO)
        return (gross - disc_total + gst).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    async def sell_bill(self, day: date, hour: int, cashier, wholesale: bool) -> None:
        from app.schemas.sales import MemberIn, SaleCreateRequest, SaleLineIn, TenderDetailIn
        from app.services import sales_service

        chosen = self.pick(RNG.randrange(3, 8) if wholesale else RNG.choice([1, 1, 2, 2, 3, 3, 4]))
        lines, gross = [], ZERO
        for pid in chosen:
            p = self.products[pid]
            held = self.stock[pid]
            want = Decimal(RNG.randrange(6, 40)) if wholesale else Decimal(RNG.choice([1, 1, 1, 2, 2, 3]))
            qty = min(want, held.to_integral_value(rounding="ROUND_FLOOR"))
            if qty <= 0:
                continue
            price = Decimal(p.price)
            if wholesale:
                price = money(Decimal(p.wholesale_price)) if getattr(p, "wholesale_price", None) else money(price * Decimal("0.93"))
            lines.append(SaleLineIn(productId=pid, qty=qty, unitPrice=price))
            gross += qty * price
        if not lines:
            return
        disc = Decimal(RNG.choice([0, 0, 0, 0, 1, 2, 3])) if not wholesale else ZERO
        total = self.bill_total(lines, disc)
        payload = {"lines": lines, "discPercent": disc, "clientRequestId": f"fc-{day:%m%d}-{self.counts['bills'] + 1}"}
        roll = RNG.random()
        if wholesale:
            party = RNG.choice(self.trade)
            payload["partyId"] = str(party.id)
            if roll < 0.5 and Decimal(party.credit_balance) + total <= Decimal(party.credit_limit):
                payload["tenders"] = {"CREDIT": total}
            else:
                payload["tenders"] = {"CASH": total + 1000}
        elif roll < 0.62:
            payload["tenders"] = {"CASH": (total // 500 + 1) * 500 if RNG.random() < 0.6 else total}
        else:
            code = "CARD" if roll < 0.8 else RNG.choice(["EASYPAISA", "JAZZCASH"])
            payload["tenders"] = {code: total}
            name = RNG.choice(BUYER_NAMES)
            payload["member"] = MemberIn(name=name, phone=f"03{RNG.choice([0, 1, 2, 3, 4])}{RNG.randrange(10000000, 99999999)}", join=True)
            payload["tenderDetails"] = {code: TenderDetailIn(reference=str(RNG.randrange(1000, 9999)) if code == "CARD" else f"03{RNG.randrange(100000000, 999999999)}")}
        when = at(day, hour, RNG.randrange(0, 59), RNG.randrange(0, 59))
        if when >= NOW - timedelta(minutes=10):
            return
        try:
            sale = await self.stamped(when, lambda: sales_service.create_sale(cashier, SaleCreateRequest(**payload)))
        except sales_service.SaleError as exc:
            # A real counter refuses a bill now and then (a locked discount, a credit limit reached): the bill isn't rung.
            self.counts["bills the counter refused"] += 1
            if self.counts["bills the counter refused"] <= 5:
                say(f"counter refused a bill: {exc.message}")
            return
        for line in lines:
            self.stock[line.productId] -= line.qty
        if wholesale and "CREDIT" in payload["tenders"]:
            await party.refresh_from_db()
        self.counts["bills"] += 1
        self.counts["wholesale bills" if wholesale else "retail bills"] += 1
        self.bills.append((sale, day, cashier))

    async def petty(self, day: date, till, cashier) -> None:
        from app.models import Account
        from app.services import till_service

        code, (low, high), what, payee = RNG.choice(PETTY)
        account = await Account.get(code=code)
        amount = RNG.randrange(low, high) // 50 * 50
        when = at(day, RNG.choice([11, 13, 15, 16]), RNG.randrange(0, 59))
        if when >= NOW - timedelta(minutes=10):
            return
        await self.stamped(when, lambda: till_service.record_movement(cashier, "out", notes_for(Decimal(amount)), what, str(account.id), payee, str(till.id)))
        self.counts["petty cash outs"] += 1

    async def give_back(self, day: date, cashier) -> None:
        from app.models import ListEntry, SaleLine
        from app.schemas.sales import ReturnCreateRequest, ReturnLineIn
        from app.services import returns_service

        earlier = [(s, d) for s, d, _c in self.bills if d < day and d >= day - timedelta(days=4) and "CASH" in [t.code for t in await s.tenders.all()]]
        if not earlier:
            return
        sale, _ = RNG.choice(earlier)
        line = RNG.choice(await SaleLine.filter(sale_id=sale.id, is_return=False))
        reason = await ListEntry.filter(kind="sale-return", active=True).first()
        request = ReturnCreateRequest(against=sale.invoice_number, lines=[ReturnLineIn(productId=str(line.product_id), qty=Decimal(1))],
                                      refundMethod="CASH", reason=reason.code if reason else None)
        when = at(day, RNG.choice([12, 16, 18]), RNG.randrange(0, 59))
        if when >= NOW - timedelta(minutes=10):
            return
        try:
            await self.stamped(when, lambda: returns_service.create_return(cashier, request))
        except returns_service.ReturnError:
            return
        self.stock[str(line.product_id)] += 1
        self.counts["customer returns"] += 1

    async def trading(self) -> None:
        self.bills = []
        day = START
        while day <= END:
            if day in (date(2026, 9, 8), date(2026, 9, 14)):
                await self.top_up(day)
            shift = [self.sellers[(day.toordinal() + i) % len(self.sellers)] for i in range(2)]
            opened = await self.open_drawers(day, shift)
            if not opened:
                break
            weekday = day.weekday()
            volume = {4: 0.7, 6: 1.3}.get(weekday, 1.0)
            retail = int(RNG.randrange(13, 21) * volume)
            hours = list(HOUR_WEIGHTS)
            moments = sorted(RNG.choices(hours, weights=[HOUR_WEIGHTS[h] for h in hours], k=retail))
            wholesale_hours = sorted(RNG.sample([10, 11, 12, 15, 16, 17], k=RNG.choice([1, 1, 2, 2, 3])))
            for hour in sorted(moments + [-h for h in wholesale_hours], key=abs):
                real_hour = abs(hour)
                on = [(t, c) for t, c, h in opened if real_hour >= h]
                if not on:
                    continue
                till, cashier = on[-1] if real_hour >= 12 and len(on) > 1 and RNG.random() < 0.5 else on[0]
                await self.sell_bill(day, real_hour, cashier, wholesale=hour < 0)
            if RNG.random() < 0.55:
                await self.petty(day, opened[0][0], opened[0][1])
            if RNG.random() < 0.3:
                await self.give_back(day, opened[-1][1])
            await self.close_drawers(day, opened)
            say(f"{day:%a %d %b}: {retail} walk-in and {len(wholesale_hours)} wholesale bills rung, drawers {'closed' if day < END else 'still open'}")
            day += timedelta(days=1)

    # ── 5. books ────────────────────────────────────────────────────────────────────────────────
    async def voucher(self, day: date, vtype: str, lines: list[tuple], description: str, reference: str | None = None, header=None, hour: int = 15):
        from app.models import Voucher
        from app.services import vouchers_service

        lines = [(a, money(dr), money(cr), d) for a, dr, cr, d in lines if money(dr) or money(cr)]
        if not lines:
            return None
        payload = {"vtype": vtype, "date": day.isoformat(), "description": description, "referenceNo": reference,
                   "headerAccountId": str(header.id) if header else None,
                   "lines": [{"accountId": str(a.id), "debit": str(dr), "credit": str(cr), "description": d} for a, dr, cr, d in lines]}
        draft = await vouchers_service.create_draft(self.bm, payload)
        posted = await vouchers_service.post(self.bm, str(draft.id))
        stamp = at(day, hour, RNG.randrange(0, 59))
        await Voucher.filter(id=posted.id).update(created_at=stamp - timedelta(minutes=RNG.randrange(3, 30)), posted_at=stamp, updated_at=stamp)
        self.counts[f"{vtype} vouchers by hand"] += 1
        return posted

    async def customer_money(self, bank) -> None:
        from app.models import Party
        from app.services import accounts_money_service
        from app.services.accounts_chart_service import customer_account

        by_name = {p.name: p for p in await Party.filter(tier="wholesale")}
        plans = [("Hassan General Store", date(2026, 9, 10), "BANK", Decimal("150000"), "IBFT 8841207733"),
                 ("Noor Kiryana Store", date(2026, 9, 16), "BANK", Decimal("40000"), "IBFT 8841990415")]
        for name, day, method, amount, ref in plans:
            party = by_name[name]
            amount = min(amount, Decimal(party.credit_balance))
            if amount <= 0:
                continue
            await self.stamped(at(day, 15, 20), lambda p=party, a=amount, m=method, r=ref: accounts_money_service.receive_payment(self.bm, str(p.id), a, m, r, "Part payment of September bills"))
            self.counts["customer payments"] += 1
        madni = by_name["Madni Wholesale Mart"]
        amount = min(Decimal("200000"), Decimal(madni.credit_balance))
        if amount > 0:
            account = await customer_account(madni)
            cheque = await self.stamped(at(date(2026, 9, 12), 16, 5), lambda: accounts_money_service.record_cheque(self.bm, {
                "direction": "received", "partyAccountId": str(account.id), "amount": str(amount), "chequeNo": "10044817",
                "drawnOn": "Meezan Bank, Hussain Agahi", "receivedOn": "2026-09-12", "chequeDate": "2026-09-14", "note": "Against September bills"}))
            await self.stamped(at(date(2026, 9, 15), 12, 30), lambda: accounts_money_service.clear_cheque(self.bm, str(cheque.id), str(bank.id), "2026-09-15"))
            self.counts["cheques received and cleared"] += 1

    async def balance(self, account, until: date) -> Decimal:
        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict(
            "SELECT COALESCE(SUM(CAST(l.debit AS REAL)),0) - COALESCE(SUM(CAST(l.credit AS REAL)),0) AS b FROM acc_voucher_lines l "
            "JOIN acc_vouchers v ON v.id = l.voucher_id WHERE v.status='posted' AND l.account_id = ? AND v.date <= ?", [str(account.id), until.isoformat()])
        return money(rows[0]["b"] or 0)

    async def books(self) -> None:
        from app.models import GRN, Account
        from app.services import accounts_posting_service
        from app.services.accounts_chart_service import Resolver, supplier_account

        result = await accounts_posting_service.run(full=True)
        for problem in result.get("problems", [])[:10]:
            say(f"posting problem: {problem}")
        acc = Resolver()
        A = {k: await acc.key(k) for k in ("cash.main", "cash.counter", "bank.main", "wallet.card", "wallet.easypaisa", "wallet.jazzcash",
                                           "tax.wht_payable", "expense.bank_charges")}
        for code in ("52040001", "52020001", "52030001", "52030002", "52030004", "52010006", "52010008", "53010002", "21040001", "21040002",
                     "12010001", "12010002", "12010003", "12010005", "12020001", "11060003"):
            A[code] = await Account.get(code=code)
        safe, bank = A["cash.main"], A["bank.main"]

        # Day one: head office opens the branch. It sends the money and pays for the fit-out and the rent deposit, and both books
        # record it against their accounts with each other: here the head office current account, at head office Fort Colony's
        # branch current account (load_branch_story.py posts that side). A branch has no capital of its own.
        A["interoffice.head_office"] = await acc.key("interoffice.head_office")
        self.funding = [(safe, 200000, "Cash for the safe and the opening floats"), (bank, 2600000, "Bank transfer from head office"),
                        (A["12010001"], 1250000, "Shelving, counters and fixtures, paid by head office"),
                        (A["12010002"], 420000, "POS terminals, scanners and a scale, paid by head office"),
                        (A["12010003"], 290000, "Counter PCs and printers, paid by head office"),
                        (A["12010005"], 760000, "Chillers and air conditioning, paid by head office"),
                        (A["11060003"], 300000, "Rent security deposit, paid to the landlord by head office")]
        funded = sum((Decimal(v) for _a, v, _t in self.funding), ZERO)
        await self.voucher(START, "JV", [(a, v, 0, t) for a, v, t in self.funding] + [(A["interoffice.head_office"], 0, funded, "Put in by head office")],
                           "Head office opens Fort Colony: cash, bank, fit-out and the rent deposit", reference="HO-FUNDING-FC", hour=9)
        self.funded = funded

        # rent, internet, utilities and salaries to the middle of the month
        await self.voucher(date(2026, 9, 1), "JV", [(A["52040001"], 85000, 0, "Shop rent for September 2026"), (bank, 0, 76500, "Cheque to the landlord"),
                                                     (A["tax.wht_payable"], 0, 8500, "10% withholding tax on rent")], "Rent for September 2026, withholding tax deducted", "RENT-SEP-2026-FC", hour=11)
        await self.voucher(date(2026, 9, 5), "BPV", [(A["52030004"], 4999, 0, "PTCL fibre internet and landline, September")], "Internet and phone bill", header=bank, reference="PTCL-SEP-FC", hour=12)
        await self.voucher(MID, "JV", [(A["52020001"], 95000, 0, "Salaries 1 to 15 September, 7 staff"), (A["52030001"], 14200, 0, "Electricity 1 to 15 September (meter reading)"),
                                       (A["52010008"], 11300, 0, "Depreciation 1 to 15 September"), (A["21040001"], 0, 95000, "Owed to staff"),
                                       (A["21040002"], 0, 14200, "Electricity owed"), (A["12020001"], 0, 11300, "Depreciation")],
                           "Costs to 15 September accrued for the mid-month review", "ACCRUAL-2026-09-15-FC", hour=13)
        await self.voucher(MID, "CPV", [(A["52010006"], 4000, 0, "Night guard and sweeper, 1 to 15 September")], "Cleaning and security contractor", header=safe, reference="CLEAN-SEP-FC", hour=11)

        # wholesale customers paying what they owe: a bank transfer, a cheque received and cleared, another transfer
        await self.customer_money(bank)

        # suppliers paid on their terms, Wednesdays and Saturdays
        paid = set()
        day = START
        while day <= END:
            if day.weekday() in (2, 5):
                owed: dict[str, list] = defaultdict(list)
                for grn in await GRN.filter(due_date__lte=day + timedelta(days=3)).prefetch_related("supplier").order_by("at"):
                    if grn.id in paid or grn.at.astimezone(PKT).date() >= day:
                        continue
                    owed[grn.supplier.code].append(grn)
                    paid.add(grn.id)
                for code, grns in owed.items():
                    supplier = grns[0].supplier
                    total = sum((Decimal(g.net_total or 0) for g in grns), ZERO)
                    if total <= 0:
                        continue
                    account = await supplier_account(supplier)
                    text = "; ".join(f"{g.grn_number} ({g.party_inv_no})" for g in grns)[:250]
                    await self.voucher(day, "BPV", [(account, total, 0, text)], f"Online transfer to {supplier.name}", header=bank,
                                       reference=f"IBFT{RNG.randrange(10**8, 10**9)}", hour=15)
                    self.counts["supplier payments"] += 1
            day += timedelta(days=1)

        # card and wallet takings reach the bank the next day, less fees; safe cash banked three times a week
        fees = {"wallet.card": Decimal("0.018"), "wallet.easypaisa": Decimal("0.01"), "wallet.jazzcash": Decimal("0.01")}
        day = START + timedelta(days=1)
        while day <= END:
            if day.weekday() != 6:
                lines = []
                for key, rate in fees.items():
                    held = await self.balance(A[key], day - timedelta(days=1))
                    if held > 0:
                        fee = money(held * rate)
                        lines += [(bank, held - fee, 0, "Takings settled"), (A["53010002"], fee, 0, "Fee"), (A[key], 0, held, "Settled to the bank")]
                if lines:
                    await self.voucher(day, "JV", lines, "Card and wallet takings settled to the bank, less fees", f"SETTLE-{day:%d%m}-FC", hour=12)
            if day.weekday() in (0, 2, 4):
                # The drawers' takings go to the safe at close; the safe keeps Rs 75,000 and the rest goes to the bank.
                held = await self.balance(safe, day - timedelta(days=1))
                amount = (held - Decimal("75000")) // 1000 * 1000
                if amount >= 20000:
                    await self.voucher(day, "CV", [(bank, amount, 0, "Cash from the safe deposited"), (safe, 0, amount, "Cash from the safe deposited")],
                                       "Cash from the safe deposited in the bank", f"DEP-{day:%d%m}-FC", hour=17 if day < END else 11)
            day += timedelta(days=1)
        await accounts_posting_service.run(full=True)

    async def renumber(self) -> None:
        from app.models import Counter, OutboxEvent, Voucher
        from app.services import vouchers_service

        prefix = await vouchers_service.book_prefix()
        rows = sorted(await Voucher.all(), key=lambda v: (v.date, v.posted_at or v.created_at, str(v.id)))
        per_type: dict[str, int] = defaultdict(int)
        planned = []
        for v in rows:
            per_type[v.vtype] += 1
            planned.append((v, f"{prefix}-{v.vtype}-{per_type[v.vtype]:06d}"))
        for index, (v, _n) in enumerate(planned):
            await Voucher.filter(id=v.id).update(number=f"TMP-{index:07d}")
        for v, number in planned:
            fields = {"number": number}
            if v.auto:
                stamp = at(v.date, 22, 45) if v.date < END else NOW - timedelta(minutes=5)
                fields.update(created_at=stamp, posted_at=stamp, updated_at=stamp)
            await Voucher.filter(id=v.id).update(**fields)
        for vtype, count in per_type.items():
            await Counter.filter(id=f"voucher:{vtype}").delete()
            await Counter.create(id=f"voucher:{vtype}", value=count + 1)
        await OutboxEvent.filter(aggregate_type="AccVoucher").delete()
        for v in await Voucher.filter(status="posted").order_by("date", "created_at"):
            await vouchers_service.emit(v)
        self.counts["vouchers"] = len(planned)

    # ── 6. what the branch server would send ────────────────────────────────────────────────────
    async def export(self, path: Path) -> None:
        from app.models import OutboxEvent
        from app.services import accounts_reports_service, snapshot_service  # noqa: F401

        aggregates = await snapshot_service.build_aggregates()
        # Only the Items Fort Colony has ever stocked: a branch that carries a few hundred of the catalog's Items isn't "out of
        # stock" of the other forty-odd thousand, it just doesn't carry them.
        carried = [str(r["p"]) for r in await Tortoise.get_connection("default").execute_query_dict("SELECT DISTINCT product_id AS p FROM stock_movements")]
        rows = await snapshot_service.stock_rows(carried)
        order = {"AccSettings": 0, "AccChart": 1, "AccVoucher": 2, "Activity": 3}
        events = []
        for e in await OutboxEvent.filter(aggregate_type__in=list(order)).order_by("created_at"):
            events.append({"id": str(e.id), "aggregateType": e.aggregate_type, "aggregateId": e.aggregate_id, "payload": e.payload,
                           "originUserId": e.origin_user_id, "originDeviceId": e.origin_device_id,
                           "createdAt": e.created_at.isoformat() if e.created_at else None})
        events.sort(key=lambda e: (order[e["aggregateType"]], e["createdAt"] or ""))
        # Head office's own side of opening the branch, for load_branch_story.py: the owner's added capital and the funding.
        head_office = {"day": START.isoformat(), "ownerCapital": "2500000", "reference": "HO-FUNDING-FC",
                       "funding": [{"amount": "200000", "text": "Cash for Fort Colony's safe and opening floats"},
                                   {"amount": "2600000", "text": "Bank transfer to Fort Colony"},
                                   {"amount": "2720000", "text": "Fort Colony's shelving, POS, computers, chillers and air conditioning"},
                                   {"amount": "300000", "text": "Fort Colony's rent security deposit, paid to the landlord"}]}
        path.write_text(json.dumps({"branch": FC["code"], "aggregates": aggregates, "stock": rows, "events": events, "headOffice": head_office},
                                   default=str), encoding="utf-8")
        say(f"wrote {path}: {len(aggregates.get('daily', []))} trading days, {len(rows)} stock rows, {len(events)} events for head office")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="write it (on a copy)")
    parser.add_argument("--export", help="where to write what head office receives", required=True)
    args = parser.parse_args()
    target = settings.db_url.replace("sqlite://", "")
    live = (Path(__file__).resolve().parents[1] / "branch.db").resolve()
    if Path(target).resolve() == live:
        raise SystemExit("This runs on a copy of a branch database, never on the live branch.db. Point DB_URL at a copy.")
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        from app.models import Counter

        if await Counter.exists(id=KEY):
            story = FortColony()
            await story.export(Path(args.export))
            return
        if not args.yes:
            print(__doc__)
            print("  Dry run: nothing written. Run with --yes on a copy.")
            return
        story = FortColony()
        await story.load_columns()
        steps = [
            ("What Model Town sold in September", story.model_town_september),
            ("The copy becomes Fort Colony", story.become_fort_colony),
            ("What Fort Colony carries", story.assortment),
            ("First stock from the suppliers", story.first_stock),
            ("Trading, 1 to 17 September", story.trading),
        ]
        if await Counter.exists(id=KEY + ":traded"):
            # Trading already on this copy (a run that stopped in the books): carry on from there.
            from app.models import User

            story.bm = await User.get(title="Branch Manager")
            steps = []
        steps += [("The books", story.books), ("Voucher numbers in date order", story.renumber)]
        for title, step in steps:
            say(f"{title} ...")
            await step()
            if step == story.trading:
                await Counter.create(id=KEY + ":traded", value=1)
                checkpoint = Path(args.export).with_name("fc-traded.db")
                checkpoint.unlink(missing_ok=True)
                await Tortoise.get_connection("default").execute_query("VACUUM INTO ?", [str(checkpoint)])
        await Counter.create(id=KEY, value=1)
        print("\n  Done:")
        for key, value in sorted(story.counts.items()):
            print(f"    {key}: {value}")
        await story.export(Path(args.export))
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
