# D.Marina — Branch App State Report

**As of:** 12 September 2026
**Scope:** Branch Server (`backend/branch-server/`) + Branch App (`branch-app/`) only. Cloud Server/Cloud App and cross-server sync are separately tracked and not covered here (see "Out of scope" at the end).

---

## 1. What this covers

Branch is the single-store side of D.Marina: the counter (billing, till), the stockroom (receiving, counts, adjustments, transfers-in), and the branch office (staff/access, approvals, reports). Everything in this document is real — a running FastAPI backend, a real SQLite database with a real 47,465-product catalog imported from actual legacy Excel/xls exports, and a React frontend wired to it end to end. Nothing described as "done" below is a mock, a stub returning fake data, or `localStorage` pretending to be a server.

---

## 2. Users & roles

Ten users across five roles (five originally seeded, five added when generating this session's test dataset):

| Role | Users | Default access |
|---|---|---|
| **Cashier** | Cashier, Bilal Ahmed, Usman Tariq | Everything under `store.*` |
| **Sales Manager** | Sales Manager, Hina Malik | Everything under `store.*` (same as Cashier, plus discount-override authority) |
| **Stock Keeper** | Stock Keeper, Kashif Iqbal | Everything under `inventory.*` |
| **Inventory Manager** | Inventory Manager, Sara Khan | Everything under `inventory.*` + `reports` |
| **Branch Manager** | Branch Manager | Everything under `branch-console.*` + `reports` |

Login is `<role-slug>@branch.dmarina.pk` (e.g. `cashier2@branch.dmarina.pk`) with a role-named password (e.g. `cashier123`) — see `backend/branch-server/app/services/seed_service.py` and `scripts/seed_august_demo_data.py` for the exact list.

### RBAC — how access actually works

This is **not** a role allowlist. Every user has their own row-level `UserPermission` set (resource × Read/Write/Execute), seeded from their role's template at creation but editable per-user afterward. The role templates above describe the *default*, not a ceiling — a Branch Manager can grant or narrow any individual user's access on any resource.

- **Delegated management, not a superadmin:** Branch Manager holds `branch-console.staff-access` (R/W) and manages every user's permissions from Staff & Roles — `GET/PUT /rbac/users/:id/permissions`, live-checked, never cached in the JWT.
- **Self-escalation is blocked:** `PUT /users/:id/permissions` rejects a user editing their own id, server-side, verified live.
- **Enforcement is real and per-request:** every protected endpoint checks the caller's actual `UserPermission` rows at request time. Narrowing a user's permissions takes effect on their *next* request — no re-login, no cache to bust, no client-side-only gate to route around.
- **Resources are granular**, not per-module: `inventory.movements` and `inventory.batches` are independently grantable (a user can read one without the other); `store.billing` and `store.returns` are independently grantable (a returns-only cashier doesn't need billing access); `inventory.counts` (submit) and `inventory.counts.approve` (decide) are separate resources, so a submitter and their approver are genuinely different grants, not the same permission read two ways.

---

## 3. Backend — what exists, by domain

Stack: FastAPI + Tortoise ORM + Aerich migrations + Pydantic + `pydantic-settings`. Layering is fixed: **Route → Middlewares → Controllers → Schemas → Services** — Services are the only layer touching the ORM. SQLite for now; every model uses dialect-neutral field types so a later move to Postgres/SQL Server is a `db_url` change, not a rewrite. Port 4174, binds to `0.0.0.0` (LAN-visible) with a real device-identity middleware (`X-Device-Id`) already in place for the sync work ahead.

### Auth & RBAC
`POST /auth/login`, `POST /auth/logout`, `GET /me`, `GET /rbac/resources`, `GET /rbac/users`, `GET/PUT /rbac/users/:id/permissions`. JWT (PyJWT) + bcrypt. Permissions resolved live from `UserPermission` on every request, never baked into the token.

### Store domain
| Feature | Endpoints | Notes |
|---|---|---|
| Till | `GET /till/current`, `POST /till/open`, `POST /till/cash-in`, `POST /till/cash-out`, `GET /till/close/preview`, `POST /till/close`, **`GET /till/sessions`** | Net Cash is computed server-side from the branch's own sale/return/movement rows, never trusted from the client. `GET /till/sessions` (new) lists every closed session branch-wide, paginated. |
| Sales | `GET /sales/next-invoice-number`, `POST /sales`, `GET /sales/:invoiceNumber`, **`GET /sales`** | `POST /sales` is one atomic transaction: sale + lines + stock movements + voucher redemption + outbox event, all-or-nothing. Idempotent via an optional client-supplied `clientRequestId` — a retried request returns the already-committed sale instead of double-billing. Server re-derives every total (gross/discount/GST/net) rather than trusting the client's numbers. `GET /sales` (new) lists branch-wide, paginated, date-filterable. |
| Returns | `POST /returns` | Caps each line at what's actually still returnable *per invoice/product*, across every prior return against that invoice — the double-refund gap found in review is closed. |
| Parties | `GET/POST/PATCH /parties`, `POST /parties/import` | Credit-limit enforcement is real (`creditAllowed`/`creditLimit`/`creditBalance` checked and updated on every credit sale) — the legacy system tracked these fields but never enforced them. |
| Held Bills | `GET/POST/DELETE /held-bills` | |
| Gift Vouchers | `GET /gift-vouchers/:code`, `POST /gift-vouchers`, `POST /gift-vouchers/:code/redeem`, **`GET /gift-vouchers`** | Redemption happens as a `VOUCHER` tender inside `POST /sales`, not a separate call. `GET /gift-vouchers` (new) lists every voucher branch-wide, paginated. |

### Inventory domain
| Feature | Endpoints | Notes |
|---|---|---|
| Movements ledger | `GET /inventory/movements`, `GET /inventory/balance` | Balance is **always folded from the movement ledger**, never stored — the single most important invariant in this domain, and it holds. **Known gap:** `GET /inventory/movements` has no pagination — see §6. |
| Receiving (GRN) | `POST /inventory/grn`, **`GET /inventory/grns`** | Bonus quantity genuinely increases stock (dilutes weighted-average cost, never raises it) — a real gap in the legacy costing model that's closed here. `GET /inventory/grns` (new) lists branch-wide, paginated. |
| Batches & Expiry | `GET /inventory/batches` | FEFO-pick logic lives in the frontend over this data. Same pagination gap as movements, at present much lower volume. |
| Physical Counts | `POST /inventory/counts`, `POST /inventory/counts/:id/approve`, **`GET /inventory/counts`** | Approval posts exactly the delta as a `count-correction` movement (or nothing, if the delta is zero). `GET /inventory/counts` (new) lists branch-wide, paginated, gated so a submitter, an approver, and a Branch Manager doing oversight can all read it even though none of those is a strict subset of another. |
| Adjustments | `POST /inventory/adjustments`, `POST /inventory/adjustments/:id/approve`, `POST /inventory/adjustments/:id/reject`, **`GET /inventory/adjustments`** | Sign (damage/expiry = out, found = in) is derived at *approval* time from the reason, not at submission time. Same branch-wide list pattern as Counts. |
| Transfers (inbound) | `GET /inventory/transfers`, `POST /inventory/transfers/:id/dispute` | Branch-side (receiving) half only — the Warehouse (dispatch) half lives on Cloud Server, which doesn't exist yet (§7), so this will stay empty of *new* data until then. Seeded transfer history is visible today. |

### Catalog
| Feature | Endpoints | Notes |
|---|---|---|
| Products | `GET/POST /catalog/products` (paginated, searchable), `POST /catalog/products/import`, `POST /catalog/products/import-aliases`, `GET /catalog/products/lookup?code=` | Real product taxonomy (department/category/class/subclass/manufacturer/brand), a third price (`rpp`), and alternate-barcode aliases (`ProductAlias`, one product → many scannable codes) were all added specifically to match the legacy data, not invented. Bulk import handles `.csv`, `.xlsx`, and legacy `.xls`, with per-row error reporting and upsert-by-natural-key. `lookup?code=` resolves SKU, barcode, or any alias code in one call — built for a scanner/exact-entry flow. |
| Suppliers | `GET/POST /suppliers`, `POST /suppliers/import` | |

### Sync plumbing (built, not yet wired to anything)
Every write in every domain above creates an `OutboxEvent` row in the same atomic transaction as the business data. `Device` identity is captured on every request via `X-Device-Id`. This is the substrate the Branch→Cloud sync protocol (I7) will consume — nothing reads it yet because Cloud Server doesn't have the receiving end built.

---

## 4. Frontend — what's wired, screen by screen

Every screen below reads real data from the endpoints in §3 and writes through them — no `localStorage` business data remains except two narrow, load-bearing exceptions noted inline.

| Module | Screen | Status |
|---|---|---|
| **Store** | Billing | ✅ Full code-entry POS: scan/type, quantity multiplier (`3*code`), loose/weighed items, party attach, retail/wholesale tiers, discounts with manager-override (F9), gift-voucher tender, mixed tenders, receipt. Idempotency key generated per bill attempt. |
| | Till (Open/Cash In/Cash Out/Close) | ✅ Denomination-based counting throughout; Close previews the server's own Net Cash before committing. |
| | X/Z · Day Close | ✅ Branch-wide (not per-terminal) as of this session — reads `GET /sales` + `GET /till/sessions` for the day. |
| | Hold / Recall | ✅ |
| | Returns | ✅ Enforces the same per-invoice remaining-returnable cap as the backend, surfaced as a clear error rather than a silent overshoot. |
| | Gift Vouchers | ✅ Branch-wide list (was terminal-scoped until this session) + issue + code lookup. |
| | Counter Board | ⛔ **Stub.** No backend model or endpoint exists for shift/till assignment either — this is unbuilt on both sides, not just unwired. |
| | Staff on Duty | ⛔ **Stub**, same reason. |
| **Inventory** | Stock Overview | ✅ Reworked this session to enumerate from the full movements ledger rather than the (capped) catalog page, so a product with real stock is never invisible regardless of catalog size. |
| | Receiving (GRN) | ✅ Server-search item picker (not limited to the first 200 catalog rows); branch-wide recent-GRNs list. |
| | Movements | ✅ Correct data; **see §6 for a real scaling caveat.** |
| | Batches & Expiry | ✅ FEFO-pick flagging. |
| | Physical Counts | ✅ Branch-wide submitted-counts list; approve gated correctly. |
| | Adjustments | ✅ Branch-wide list; approve/reject gated correctly. |
| | Transfers (inbound) | ✅ Wired; will show new data only once Cloud Server's dispatch side exists. |
| | Product Catalog | ✅ Real 47,465-row catalog, paginated + searched server-side; bulk import UI (.csv/.xlsx/.xls) with per-row error reporting. |
| | Suppliers | ✅ Same import pattern as Products. |
| **Branch Console** | Dashboard | ✅ Today's sales/till/stock-health, each panel with its own loading/error state (fixed this session — previously could show a false "all healthy" before data loaded, or permanently if one specific fetch failed while others succeeded). |
| | Approvals Inbox | ✅ Branch-wide adjustments/counts pending + a discount-override log; each tab loads and errors independently. |
| | Staff & Roles | ✅ The RBAC delegation UI itself — per-user resource grants, self-escalation blocked — plus branch-wide drawer-variance-by-cashier. |
| | Customer Registry | ✅ Parties CRUD + bulk import. |
| **Reports** | Sales Reports | ✅ Today/7-day/all-time, branch-wide, tender breakdown, top products by revenue. |
| | Inventory Reports | ✅ Stock lines/value/ledger-entry counts, loading/error states independent of the Sales tab. |
| | Staff & Work | ✅ Tills closed, adjustments/counts approved & pending, drawer variance by cashier — branch-wide. |

**The two remaining `localStorage`-only pieces**, both explicit, disclosed, and low-stakes:
- `useSales`'s original terminal-local echo (`recordSale`/`recordReturn`) — superseded as a *read* source everywhere above, but the write side was left in place rather than ripped out; it's dead weight, not a correctness risk.
- Sidebar-collapsed and similar pure UI-state conveniences, which were never meant to be server-side.

---

## 5. Data on the branch right now

- **47,465 real products** imported from the actual legacy Excel/xls exports (`D2 PHARMACY ITEMS NAME LIST.xlsx`, `D2 ALTERNATE BARCODE LIST ALL DATA D2.xls`, `D2 GENERAL ITEMS WITHOUT PHARMACY WITHOUT ALTERNATE BARCODE.xlsx`) — 25,364 tagged Pharmacy, 1,774 alternate-barcode aliases.
- **A full simulated month, 1–31 August 2026:** the whole catalog received into opening stock (one GRN, `GRN-0014`, 47,465 lines) on 1 August, then 345 sales, 28 partial sales-returns, 31 complete till cycles (open → sales → cash movements → close), 6 physical counts, 6 adjustments, and 5 restock GRNs — spread across all 5 store-facing/stock-facing users, with real (not placeholder) numbers: server-computed invoice totals, weighted-average costing, a real credit balance walked from Rs 120,000 to Rs 315,577 against Ali Traders' Rs 500,000 limit, real drawer variances per cashier. The generating script is `backend/branch-server/scripts/seed_august_demo_data.py` — re-runnable against a fresh copy of the real catalog, not idempotent against itself (see the script's own docstring).
- The 4 real sales made during earlier live testing (11–12 September) are untouched by any of the above.
- **Note:** `branch.db` itself is gitignored, as it always has been — pushing this repo does **not** push the database or this dataset. Anyone who needs the same August dataset on another machine runs the script themselves after importing the real catalog.

---

## 6. Known gaps (honest, not hidden)

1. **`GET /inventory/movements` and `GET /inventory/batches` have no pagination.** With the August dataset, movements now returns a 10.4MB JSON payload in ~2.6s — not broken, but the same class of problem `/sales` and `/catalog/products` already hit and were fixed for. Worth doing before this goes in front of anyone outside the team.
2. **No "purchase returns" (return-to-supplier) feature exists** — neither a model, a service, nor an endpoint. Only receiving (GRN) exists on the purchase side. If this is genuinely needed for the branch MVP, it's a real feature to design and build, not a wiring gap.
3. **Counter Board and Staff on Duty are stubs** on both sides — no backend model, explicit "coming soon" on the frontend.
4. **Transfers (inbound) will stay quiet** until Cloud Server's Warehouse domain (dispatch side) exists — there is nothing wrong with the branch side, there's just no counterpart yet to send it anything new.
5. **No cross-server sync exists yet.** Every `OutboxEvent` row is being written correctly and is ready to be consumed, but nothing reads the outbox — a branch and the (not-yet-built) Cloud Server are fully independent today.
6. **A small residual idempotency race**: `POST /sales` closes the common case (a delayed/lost-response retry) but a true *simultaneous* double-fire of the same request could still race past the check before either commits. Accepted as low-probability for a single-till-single-cashier flow; documented in code.

---

## 7. Out of scope for this document

Cloud Server has only Health + Auth/RBAC (the same starting point Branch had before its Store/Inventory domains were built) — no Warehouse domain, no Executive/Admin domain, both entirely unbuilt. Cloud App is 100% `localStorage`, wired to nothing. Cross-server sync (branch → cloud push, idempotent ingestion, checkpoints, quarantine) hasn't been started. See `.loop_backend/backend-plan.md` (local to the project lead's machine, not in this repo) for the full iteration plan covering that work.
