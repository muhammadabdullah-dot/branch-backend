# Agreeing a bank account's books with the bank's statement.
#
# Their own software has this screen and its table holds nothing: not one of their 1,330 accounts was ever reconciled
# in it. This is built because a shop with a bank account needs it, not because they used it.
#
# The tick on a voucher line is nullable and written inline, as migrations 6, 20, 30 and 32 were: aerich emits
# `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY`, which SQLite has no syntax for, and SQLite does accept a
# REFERENCES clause on an added column while that column is nullable.
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "acc_bank_reconciliations" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "number" VARCHAR(20) NOT NULL UNIQUE,
    "up_to" DATE NOT NULL,
    "statement_balance" VARCHAR(40) NOT NULL,
    "book_balance" VARCHAR(40),
    "status" VARCHAR(10) NOT NULL DEFAULT 'open',
    "closed_at" TIMESTAMP,
    "closed_by_name" VARCHAR(120),
    "note" VARCHAR(255),
    "created_by_name" VARCHAR(120),
    "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "account_id" CHAR(36) NOT NULL REFERENCES "acc_accounts" ("id") ON DELETE RESTRICT
);
        CREATE INDEX IF NOT EXISTS "idx_acc_bank_rec_account" ON "acc_bank_reconciliations" ("account_id", "up_to");
        ALTER TABLE "acc_voucher_lines" ADD COLUMN "reconciliation_id" CHAR(36) REFERENCES "acc_bank_reconciliations" ("id") ON DELETE SET NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "acc_voucher_lines" DROP COLUMN "reconciliation_id";
        DROP TABLE IF EXISTS "acc_bank_reconciliations";"""
