from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "acc_depreciation_runs" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "book" VARCHAR(20) NOT NULL,
    "month" DATE NOT NULL,
    "status" VARCHAR(10) NOT NULL,
    "total" VARCHAR(40) NOT NULL,
    "created_by_name" VARCHAR(120),
    "created_at" TIMESTAMP NOT NULL,
    "updated_at" TIMESTAMP NOT NULL,
    "created_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE SET NULL,
    "voucher_id" CHAR(36) REFERENCES "acc_vouchers" ("id") ON DELETE SET NULL
) /* One month's depreciation, prepared as a draft journal. A month is taken while its voucher is a draft or posted. */;
CREATE INDEX IF NOT EXISTS "idx_acc_depreci_month_ad76eb" ON "acc_depreciation_runs" ("month", "status");
        CREATE TABLE IF NOT EXISTS "acc_fixed_assets" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "code" VARCHAR(20) NOT NULL UNIQUE,
    "name" VARCHAR(160) NOT NULL,
    "purchase_date" DATE NOT NULL,
    "cost" VARCHAR(40) NOT NULL,
    "salvage_value" VARCHAR(40) NOT NULL,
    "useful_life_months" INT NOT NULL,
    "method" VARCHAR(10) NOT NULL,
    "rate_percent" VARCHAR(40),
    "depreciation_start" DATE NOT NULL,
    "opening_accumulated" VARCHAR(40) NOT NULL,
    "location" VARCHAR(120),
    "supplier_ref" VARCHAR(120),
    "notes" VARCHAR(500),
    "status" VARCHAR(10) NOT NULL,
    "disposal_date" DATE,
    "disposal_proceeds" VARCHAR(40),
    "created_by_name" VARCHAR(120),
    "created_at" TIMESTAMP NOT NULL,
    "updated_at" TIMESTAMP NOT NULL,
    "accumulated_account_id" CHAR(36) NOT NULL REFERENCES "acc_accounts" ("id") ON DELETE RESTRICT,
    "category_account_id" CHAR(36) NOT NULL REFERENCES "acc_accounts" ("id") ON DELETE RESTRICT,
    "disposal_account_id" CHAR(36) REFERENCES "acc_accounts" ("id") ON DELETE SET NULL,
    "disposal_voucher_id" CHAR(36) REFERENCES "acc_vouchers" ("id") ON DELETE SET NULL,
    "expense_account_id" CHAR(36) NOT NULL REFERENCES "acc_accounts" ("id") ON DELETE RESTRICT
);
        CREATE TABLE IF NOT EXISTS "acc_depreciation_run_lines" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "amount" VARCHAR(40) NOT NULL,
    "accumulated_after" VARCHAR(40) NOT NULL,
    "asset_id" CHAR(36) NOT NULL REFERENCES "acc_fixed_assets" ("id") ON DELETE RESTRICT,
    "run_id" CHAR(36) NOT NULL REFERENCES "acc_depreciation_runs" ("id") ON DELETE CASCADE
);
        ALTER TABLE "cheques" ADD "redeposit_of_id" VARCHAR(36);
        ALTER TABLE "customer_payments" ADD "voided_at" TIMESTAMP;
        ALTER TABLE "customer_payments" ADD "voided_by_name" VARCHAR(120);
        ALTER TABLE "customer_payments" ADD "void_reason" VARCHAR(255);
        ALTER TABLE "suppliers" ADD "cnic" VARCHAR(40);
        ALTER TABLE "suppliers" ADD "discount_percent" VARCHAR(40) NOT NULL DEFAULT 0;
        ALTER TABLE "suppliers" ADD "phone2" VARCHAR(30);
        ALTER TABLE "suppliers" ADD "updated_at" TIMESTAMP;
        ALTER TABLE "suppliers" ADD "origin" VARCHAR(20);
        ALTER TABLE "suppliers" ADD "rev" INT NOT NULL DEFAULT 0;
        ALTER TABLE "suppliers" ADD "company_id" VARCHAR(40);
        CREATE UNIQUE INDEX "uid_suppliers_company_ca74a3" ON "suppliers" ("company_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_suppliers_company_ca74a3";
        ALTER TABLE "cheques" DROP COLUMN "redeposit_of_id";
        ALTER TABLE "customer_payments" DROP COLUMN "voided_at";
        ALTER TABLE "customer_payments" DROP COLUMN "voided_by_name";
        ALTER TABLE "customer_payments" DROP COLUMN "void_reason";
        ALTER TABLE "suppliers" DROP COLUMN "cnic";
        ALTER TABLE "suppliers" DROP COLUMN "discount_percent";
        ALTER TABLE "suppliers" DROP COLUMN "phone2";
        ALTER TABLE "suppliers" DROP COLUMN "updated_at";
        ALTER TABLE "suppliers" DROP COLUMN "origin";
        ALTER TABLE "suppliers" DROP COLUMN "rev";
        ALTER TABLE "suppliers" DROP COLUMN "company_id";
        DROP TABLE IF EXISTS "acc_depreciation_runs";
        DROP TABLE IF EXISTS "acc_depreciation_run_lines";
        DROP TABLE IF EXISTS "acc_fixed_assets";"""


MODELS_STATE = (
    "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"
    "f/4XP+jWBw=="
)
