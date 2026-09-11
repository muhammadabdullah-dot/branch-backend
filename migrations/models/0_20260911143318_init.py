from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "counters" (
    "id" VARCHAR(60) NOT NULL PRIMARY KEY,
    "value" INT NOT NULL
);
CREATE TABLE IF NOT EXISTS "gift_vouchers" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "code" VARCHAR(20) NOT NULL UNIQUE,
    "face_value" VARCHAR(40) NOT NULL,
    "balance" VARCHAR(40) NOT NULL,
    "issued_to_name" VARCHAR(160),
    "issued_at" TIMESTAMP NOT NULL,
    "expires_at" TIMESTAMP NOT NULL,
    "status" VARCHAR(10) NOT NULL
);
CREATE TABLE IF NOT EXISTS "locations" (
    "id" VARCHAR(40) NOT NULL PRIMARY KEY,
    "name" VARCHAR(80) NOT NULL,
    "kind" VARCHAR(20) NOT NULL,
    "priority" INT NOT NULL
);
CREATE TABLE IF NOT EXISTS "outbox_events" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "aggregate_type" VARCHAR(60) NOT NULL,
    "aggregate_id" VARCHAR(60) NOT NULL,
    "payload" JSON NOT NULL,
    "origin_user_id" VARCHAR(60),
    "created_at" TIMESTAMP NOT NULL,
    "status" VARCHAR(20) NOT NULL,
    "attempt_count" INT NOT NULL,
    "last_attempt_at" TIMESTAMP,
    "last_error" TEXT
);
CREATE TABLE IF NOT EXISTS "parties" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "code" VARCHAR(20) NOT NULL UNIQUE,
    "name" VARCHAR(160) NOT NULL,
    "is_walk_in" INT NOT NULL,
    "phone" VARCHAR(30),
    "email" VARCHAR(180),
    "address" VARCHAR(255),
    "area" VARCHAR(120),
    "contact_person" VARCHAR(120),
    "ntn" VARCHAR(40),
    "cnic" VARCHAR(40),
    "s_tax_reg_no" VARCHAR(40),
    "loyalty_no" VARCHAR(40),
    "due_days" INT NOT NULL,
    "credit_allowed" INT NOT NULL,
    "credit_limit" VARCHAR(40) NOT NULL,
    "credit_balance" VARCHAR(40) NOT NULL,
    "tier" VARCHAR(20) NOT NULL,
    "active" INT NOT NULL
);
CREATE TABLE IF NOT EXISTS "held_bills" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "label" VARCHAR(120) NOT NULL,
    "lines" JSON NOT NULL,
    "held_at" TIMESTAMP NOT NULL,
    "party_id" CHAR(36) REFERENCES "parties" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "payment_methods" (
    "code" VARCHAR(20) NOT NULL PRIMARY KEY,
    "name" VARCHAR(60) NOT NULL,
    "kind" VARCHAR(20) NOT NULL
);
CREATE TABLE IF NOT EXISTS "products" (
    "id" VARCHAR(40) NOT NULL PRIMARY KEY,
    "sku" VARCHAR(40) NOT NULL UNIQUE,
    "name" VARCHAR(160) NOT NULL,
    "price" VARCHAR(40) NOT NULL,
    "tax_rate" VARCHAR(40) NOT NULL,
    "is_weighed" INT NOT NULL,
    "unit" VARCHAR(20) NOT NULL,
    "barcode" VARCHAR(40) UNIQUE,
    "pack_unit" VARCHAR(40),
    "pack_size" INT
);
CREATE TABLE IF NOT EXISTS "batches" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "lot_number" VARCHAR(60),
    "expiry" TIMESTAMP,
    "received_qty" VARCHAR(40) NOT NULL,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "roles" (
    "id" VARCHAR(40) NOT NULL PRIMARY KEY,
    "name" VARCHAR(80) NOT NULL,
    "landing" VARCHAR(120) NOT NULL
);
CREATE TABLE IF NOT EXISTS "role_default_permissions" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "resource" VARCHAR(120) NOT NULL,
    "can_read" INT NOT NULL,
    "can_write" INT NOT NULL,
    "can_execute" INT NOT NULL,
    "role_id" VARCHAR(40) NOT NULL REFERENCES "roles" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_role_defaul_role_id_b36628" UNIQUE ("role_id", "resource")
) /* Seed-time template only — copied onto a user at creation, never read at request time. */;
CREATE TABLE IF NOT EXISTS "suppliers" (
    "id" VARCHAR(40) NOT NULL PRIMARY KEY,
    "code" VARCHAR(20) NOT NULL UNIQUE,
    "name" VARCHAR(160) NOT NULL,
    "contact_person" VARCHAR(120),
    "phone" VARCHAR(30)
);
CREATE TABLE IF NOT EXISTS "transfers" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "from_warehouse" VARCHAR(120) NOT NULL,
    "status" VARCHAR(20) NOT NULL,
    "vehicle" VARCHAR(40),
    "driver" VARCHAR(80),
    "requested_at" TIMESTAMP NOT NULL,
    "dispatched_at" TIMESTAMP,
    "received_at" TIMESTAMP,
    "dispute_open" INT NOT NULL,
    "dispute_note" TEXT
);
CREATE TABLE IF NOT EXISTS "transfer_lines" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "qty_sent" VARCHAR(40) NOT NULL,
    "qty_received" VARCHAR(40),
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE,
    "transfer_id" CHAR(36) NOT NULL REFERENCES "transfers" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "users" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "name" VARCHAR(120) NOT NULL,
    "email" VARCHAR(180) NOT NULL UNIQUE,
    "password_hash" VARCHAR(255) NOT NULL,
    "active" INT NOT NULL,
    "created_at" TIMESTAMP NOT NULL,
    "role_id" VARCHAR(40) NOT NULL REFERENCES "roles" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "adjustments" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "reason" VARCHAR(20) NOT NULL,
    "magnitude" VARCHAR(40) NOT NULL,
    "notes" TEXT,
    "status" VARCHAR(10) NOT NULL,
    "at" TIMESTAMP NOT NULL,
    "decided_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE CASCADE,
    "location_id" VARCHAR(40) NOT NULL REFERENCES "locations" ("id") ON DELETE CASCADE,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE,
    "submitted_by_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "grns" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "grn_number" VARCHAR(20) NOT NULL UNIQUE,
    "party_inv_no" VARCHAR(60),
    "gst_mode" VARCHAR(20) NOT NULL,
    "advance_tax" VARCHAR(40) NOT NULL,
    "approved" INT NOT NULL,
    "at" TIMESTAMP NOT NULL,
    "location_id" VARCHAR(40) NOT NULL REFERENCES "locations" ("id") ON DELETE CASCADE,
    "received_by_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    "supplier_id" VARCHAR(40) NOT NULL REFERENCES "suppliers" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "grn_lines" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "qty" VARCHAR(40) NOT NULL,
    "bonus_qty" VARCHAR(40) NOT NULL,
    "unit_price" VARCHAR(40) NOT NULL,
    "disc_percent" VARCHAR(40) NOT NULL,
    "expiry" TIMESTAMP,
    "tax_rate" VARCHAR(40) NOT NULL,
    "grn_id" CHAR(36) NOT NULL REFERENCES "grns" ("id") ON DELETE CASCADE,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "physical_counts" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "system_qty" VARCHAR(40) NOT NULL,
    "counted_qty" VARCHAR(40) NOT NULL,
    "status" VARCHAR(10) NOT NULL,
    "at" TIMESTAMP NOT NULL,
    "approved_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE CASCADE,
    "counted_by_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    "location_id" VARCHAR(40) NOT NULL REFERENCES "locations" ("id") ON DELETE CASCADE,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "sale_records" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "invoice_number" VARCHAR(30) NOT NULL UNIQUE,
    "at" TIMESTAMP NOT NULL,
    "gross" VARCHAR(40) NOT NULL,
    "disc_total" VARCHAR(40) NOT NULL,
    "fare" VARCHAR(40) NOT NULL,
    "gst" VARCHAR(40) NOT NULL,
    "grand_total" VARCHAR(40) NOT NULL,
    "net_value" VARCHAR(40) NOT NULL,
    "earned_points" INT NOT NULL,
    "received" VARCHAR(40) NOT NULL,
    "cash_back" VARCHAR(40) NOT NULL,
    "is_credit_sale" INT NOT NULL,
    "fbr_invoice_number" VARCHAR(30) NOT NULL,
    "cashier_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    "discount_override_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE CASCADE,
    "party_id" CHAR(36) NOT NULL REFERENCES "parties" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "return_records" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "at" TIMESTAMP NOT NULL,
    "refund_total" VARCHAR(40) NOT NULL,
    "against_id" CHAR(36) NOT NULL REFERENCES "sale_records" ("id") ON DELETE CASCADE,
    "cashier_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "return_lines" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "qty" VARCHAR(40) NOT NULL,
    "unit_price" VARCHAR(40) NOT NULL,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE,
    "return_record_id" CHAR(36) NOT NULL REFERENCES "return_records" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "sale_lines" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "qty" VARCHAR(40) NOT NULL,
    "unit_price" VARCHAR(40) NOT NULL,
    "is_return" INT NOT NULL,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE,
    "sale_id" CHAR(36) NOT NULL REFERENCES "sale_records" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "sale_tenders" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "code" VARCHAR(20) NOT NULL,
    "amount" VARCHAR(40) NOT NULL,
    "sale_id" CHAR(36) NOT NULL REFERENCES "sale_records" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "stock_movements" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "kind" VARCHAR(20) NOT NULL,
    "qty" VARCHAR(40) NOT NULL,
    "reason" VARCHAR(20),
    "at" TIMESTAMP NOT NULL,
    "location_id" VARCHAR(40) NOT NULL REFERENCES "locations" ("id") ON DELETE CASCADE,
    "origin_user_id" CHAR(36) REFERENCES "users" ("id") ON DELETE CASCADE,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "till_sessions" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "session_number" VARCHAR(20) NOT NULL UNIQUE,
    "opened_at" TIMESTAMP NOT NULL,
    "closed_at" TIMESTAMP,
    "opening_float" VARCHAR(40) NOT NULL,
    "opening_denominations" JSON NOT NULL,
    "opening_notes" TEXT,
    "status" VARCHAR(10) NOT NULL,
    "net_cash" VARCHAR(40),
    "counted_cash" VARCHAR(40),
    "variance" VARCHAR(40),
    "opened_by_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "cash_movements" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "kind" VARCHAR(10) NOT NULL,
    "amount" VARCHAR(40) NOT NULL,
    "denominations" JSON NOT NULL,
    "notes" TEXT,
    "at" TIMESTAMP NOT NULL,
    "till_session_id" CHAR(36) NOT NULL REFERENCES "till_sessions" ("id") ON DELETE CASCADE,
    "user_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "user_permissions" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "resource" VARCHAR(120) NOT NULL,
    "can_read" INT NOT NULL,
    "can_write" INT NOT NULL,
    "can_execute" INT NOT NULL,
    "updated_at" TIMESTAMP NOT NULL,
    "granted_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE CASCADE,
    "user_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_user_permis_user_id_3426da" UNIQUE ("user_id", "resource")
) /* The only table any authorization check reads (contracts.md §2.3) — per-user, not per-role. */;
CREATE TABLE IF NOT EXISTS "voucher_redemptions" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "invoice_number" VARCHAR(30) NOT NULL,
    "amount" VARCHAR(40) NOT NULL,
    "at" TIMESTAMP NOT NULL,
    "voucher_id" CHAR(36) NOT NULL REFERENCES "gift_vouchers" ("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "aerich" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSON NOT NULL
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "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"
)
