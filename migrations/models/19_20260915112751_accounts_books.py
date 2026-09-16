from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "acc_types" (
    "code" VARCHAR(2) NOT NULL PRIMARY KEY,
    "name" VARCHAR(60) NOT NULL,
    "nature" VARCHAR(6) NOT NULL,
    "statement" VARCHAR(10) NOT NULL
);
        CREATE TABLE IF NOT EXISTS "acc_settings" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "fiscal_start_month" INT NOT NULL,
    "books_start" DATE,
    "locked_until" DATE,
    "tender_accounts" JSON NOT NULL,
    "last_posting_at" TIMESTAMP,
    "last_posting_note" VARCHAR(255),
    "posting_problems" JSON NOT NULL,
    "updated_at" TIMESTAMP NOT NULL
);
        CREATE TABLE IF NOT EXISTS "acc_categories" (
    "code" VARCHAR(4) NOT NULL PRIMARY KEY,
    "name" VARCHAR(80) NOT NULL,
    "type_id" VARCHAR(2) NOT NULL REFERENCES "acc_types" ("code") ON DELETE CASCADE
);
        CREATE TABLE IF NOT EXISTS "acc_groups" (
    "code" VARCHAR(6) NOT NULL PRIMARY KEY,
    "name" VARCHAR(100) NOT NULL,
    "priority" INT NOT NULL,
    "manual_code" VARCHAR(30),
    "standard" INT NOT NULL,
    "created_at" TIMESTAMP NOT NULL,
    "category_id" VARCHAR(4) NOT NULL REFERENCES "acc_categories" ("code") ON DELETE CASCADE
);
        CREATE TABLE IF NOT EXISTS "acc_sub_groups" (
    "code" VARCHAR(8) NOT NULL PRIMARY KEY,
    "name" VARCHAR(100) NOT NULL,
    "standard" INT NOT NULL,
    "group_id" VARCHAR(6) NOT NULL REFERENCES "acc_groups" ("code") ON DELETE CASCADE
);
        CREATE TABLE IF NOT EXISTS "acc_accounts" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "code" VARCHAR(12) NOT NULL UNIQUE,
    "name" VARCHAR(160) NOT NULL,
    "kind" VARCHAR(12) NOT NULL,
    "system_key" VARCHAR(80) UNIQUE,
    "party_ref" VARCHAR(60),
    "active" INT NOT NULL,
    "restricted" INT NOT NULL,
    "check_limit" INT NOT NULL,
    "balance_limit" VARCHAR(40),
    "bank_name" VARCHAR(80),
    "bank_account_no" VARCHAR(40),
    "manual_code" VARCHAR(30),
    "remarks" VARCHAR(255),
    "standard" INT NOT NULL,
    "created_at" TIMESTAMP NOT NULL,
    "updated_at" TIMESTAMP NOT NULL,
    "group_id" VARCHAR(6) NOT NULL REFERENCES "acc_groups" ("code") ON DELETE CASCADE,
    "sub_group_id" VARCHAR(8) REFERENCES "acc_sub_groups" ("code") ON DELETE SET NULL
);
        CREATE TABLE IF NOT EXISTS "cheques" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "number" VARCHAR(20) NOT NULL UNIQUE,
    "direction" VARCHAR(10) NOT NULL,
    "cheque_no" VARCHAR(30) NOT NULL,
    "drawn_on" VARCHAR(80),
    "cheque_date" DATE NOT NULL,
    "received_on" DATE NOT NULL,
    "amount" VARCHAR(40) NOT NULL,
    "status" VARCHAR(10) NOT NULL,
    "cleared_on" DATE,
    "bounced_on" DATE,
    "note" VARCHAR(255),
    "created_by_name" VARCHAR(120),
    "created_at" TIMESTAMP NOT NULL,
    "updated_at" TIMESTAMP NOT NULL,
    "bank_account_id" CHAR(36) REFERENCES "acc_accounts" ("id") ON DELETE SET NULL,
    "party_account_id" CHAR(36) NOT NULL REFERENCES "acc_accounts" ("id") ON DELETE CASCADE
);
        CREATE TABLE IF NOT EXISTS "customer_payments" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "number" VARCHAR(20) NOT NULL UNIQUE,
    "amount" VARCHAR(40) NOT NULL,
    "method" VARCHAR(12) NOT NULL,
    "reference" VARCHAR(60),
    "note" VARCHAR(255),
    "balance_after" VARCHAR(40),
    "at" TIMESTAMP NOT NULL,
    "cash_movement_id" CHAR(36) REFERENCES "cash_movements" ("id") ON DELETE SET NULL,
    "party_id" CHAR(36) NOT NULL REFERENCES "parties" ("id") ON DELETE CASCADE,
    "received_by_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
);
        CREATE TABLE IF NOT EXISTS "acc_vouchers" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "number" VARCHAR(30) NOT NULL UNIQUE,
    "vtype" VARCHAR(4) NOT NULL,
    "date" DATE NOT NULL,
    "status" VARCHAR(10) NOT NULL,
    "auto" INT NOT NULL,
    "source" VARCHAR(120) UNIQUE,
    "source_hash" VARCHAR(64),
    "reference_no" VARCHAR(60),
    "description" VARCHAR(500),
    "cheque_no" VARCHAR(30),
    "cheque_date" DATE,
    "total" VARCHAR(40) NOT NULL,
    "created_by_name" VARCHAR(120),
    "posted_by_name" VARCHAR(120),
    "posted_at" TIMESTAMP,
    "cancelled_by_name" VARCHAR(120),
    "cancelled_at" TIMESTAMP,
    "cancel_reason" VARCHAR(255),
    "reversal_of_id" VARCHAR(36),
    "reversed" INT NOT NULL,
    "version" INT NOT NULL,
    "created_at" TIMESTAMP NOT NULL,
    "updated_at" TIMESTAMP NOT NULL,
    "created_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE SET NULL,
    "header_account_id" CHAR(36) REFERENCES "acc_accounts" ("id") ON DELETE SET NULL,
    "posted_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS "idx_acc_voucher_date_3be375" ON "acc_vouchers" ("date", "status");
        CREATE TABLE IF NOT EXISTS "acc_voucher_lines" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "line_no" INT NOT NULL,
    "debit" VARCHAR(40) NOT NULL,
    "credit" VARCHAR(40) NOT NULL,
    "description" VARCHAR(255),
    "reference_no" VARCHAR(60),
    "account_id" CHAR(36) NOT NULL REFERENCES "acc_accounts" ("id") ON DELETE RESTRICT,
    "voucher_id" CHAR(36) NOT NULL REFERENCES "acc_vouchers" ("id") ON DELETE CASCADE
);
        ALTER TABLE "cash_movements" ADD "payee" VARCHAR(120);
        ALTER TABLE "cash_movements" ADD "account_id" CHAR(36) REFERENCES "acc_accounts" ("id") ON DELETE SET NULL;
        ALTER TABLE "grns" ADD "net_total" VARCHAR(40);
        ALTER TABLE "grns" ADD "tax_total" VARCHAR(40);
        ALTER TABLE "grns" ADD "disc_total" VARCHAR(40);
        ALTER TABLE "grns" ADD "gross_total" VARCHAR(40);
        ALTER TABLE "grns" ADD "due_date" DATE;
        ALTER TABLE "gift_vouchers" ADD "payment_reference" VARCHAR(60);
        ALTER TABLE "gift_vouchers" ADD "paid_by" VARCHAR(20);
        ALTER TABLE "gift_vouchers" ADD "issued_by_name" VARCHAR(120);
        ALTER TABLE "purchase_returns" ADD "total" VARCHAR(40);
        ALTER TABLE "purchase_returns" ADD "tax_total" VARCHAR(40);
        ALTER TABLE "purchase_return_lines" ADD "unit_cost" VARCHAR(40);
        ALTER TABLE "purchase_return_lines" ADD "tax_rate" VARCHAR(40);
        ALTER TABLE "return_lines" ADD "unit_cost" VARCHAR(40);
        ALTER TABLE "return_lines" ADD "tax_amount" VARCHAR(40);
        ALTER TABLE "return_records" ADD "tax_total" VARCHAR(40);
        ALTER TABLE "return_records" ADD "rounding" VARCHAR(40);
        ALTER TABLE "sale_lines" ADD "tax_amount" VARCHAR(40);
        ALTER TABLE "stock_movements" ADD "unit_cost" VARCHAR(40);
        ALTER TABLE "till_sessions" ADD "closing_denominations" JSON;
        ALTER TABLE "till_sessions" ADD "closed_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE SET NULL;
        ALTER TABLE "transfer_lines" ADD "unit_cost" VARCHAR(40);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "cash_movements" DROP COLUMN "payee";
        ALTER TABLE "cash_movements" DROP COLUMN "account_id";
        ALTER TABLE "grns" DROP COLUMN "net_total";
        ALTER TABLE "grns" DROP COLUMN "tax_total";
        ALTER TABLE "grns" DROP COLUMN "disc_total";
        ALTER TABLE "grns" DROP COLUMN "gross_total";
        ALTER TABLE "grns" DROP COLUMN "due_date";
        ALTER TABLE "gift_vouchers" DROP COLUMN "payment_reference";
        ALTER TABLE "gift_vouchers" DROP COLUMN "paid_by";
        ALTER TABLE "gift_vouchers" DROP COLUMN "issued_by_name";
        ALTER TABLE "purchase_returns" DROP COLUMN "total";
        ALTER TABLE "purchase_returns" DROP COLUMN "tax_total";
        ALTER TABLE "purchase_return_lines" DROP COLUMN "unit_cost";
        ALTER TABLE "purchase_return_lines" DROP COLUMN "tax_rate";
        ALTER TABLE "return_lines" DROP COLUMN "unit_cost";
        ALTER TABLE "return_lines" DROP COLUMN "tax_amount";
        ALTER TABLE "return_records" DROP COLUMN "tax_total";
        ALTER TABLE "return_records" DROP COLUMN "rounding";
        ALTER TABLE "sale_lines" DROP COLUMN "tax_amount";
        ALTER TABLE "stock_movements" DROP COLUMN "unit_cost";
        ALTER TABLE "till_sessions" DROP COLUMN "closing_denominations";
        ALTER TABLE "till_sessions" DROP COLUMN "closed_by_id";
        ALTER TABLE "transfer_lines" DROP COLUMN "unit_cost";
        DROP TABLE IF EXISTS "cheques";
        DROP TABLE IF EXISTS "acc_types";
        DROP TABLE IF EXISTS "acc_vouchers";
        DROP TABLE IF EXISTS "customer_payments";
        DROP TABLE IF EXISTS "acc_sub_groups";
        DROP TABLE IF EXISTS "acc_groups";
        DROP TABLE IF EXISTS "acc_accounts";
        DROP TABLE IF EXISTS "acc_voucher_lines";
        DROP TABLE IF EXISTS "acc_settings";
        DROP TABLE IF EXISTS "acc_categories";"""


MODELS_STATE = (
    "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"
    "o52J1g=="
)
