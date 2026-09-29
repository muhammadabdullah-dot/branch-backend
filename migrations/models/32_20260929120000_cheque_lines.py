# Which invoices a cheque pays, and how much of each.
#
# One cheque settles several invoices: 3,616 of the old software's 4,010 post dated cheques name the invoices they
# cover, one of them naming ten. Their lines add up to the cheque exactly on all 3,616, which is the rule the service
# enforces; 394 of their cheques carry no lines at all, so lines stay optional.
#
# The link to our own delivery is nullable and written inline, as migrations 6, 20 and 30 were: aerich emits
# `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY`, which SQLite has no syntax for.
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "cheque_lines" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "line_no" INT NOT NULL DEFAULT 1,
    "invoice_no" VARCHAR(60) NOT NULL,
    "invoice_date" DATE,
    "invoice_amount" VARCHAR(40) NOT NULL DEFAULT 0,
    "outstanding_before" VARCHAR(40) NOT NULL DEFAULT 0,
    "return_amount" VARCHAR(40) NOT NULL DEFAULT 0,
    "paid_amount" VARCHAR(40) NOT NULL,
    "note" VARCHAR(255),
    "cheque_id" CHAR(36) NOT NULL REFERENCES "cheques" ("id") ON DELETE CASCADE,
    "grn_id" CHAR(36) REFERENCES "grns" ("id") ON DELETE SET NULL,
    CONSTRAINT "uid_cheque_lines_cheque__line_no" UNIQUE ("cheque_id", "line_no")
);
        CREATE INDEX IF NOT EXISTS "idx_cheque_lines_invoice_no" ON "cheque_lines" ("invoice_no");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "cheque_lines";"""
