from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "purchase_returns" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "return_number" VARCHAR(20) NOT NULL UNIQUE,
    "reason" VARCHAR(20) NOT NULL,
    "notes" TEXT,
    "at" TIMESTAMP NOT NULL,
    "grn_id" CHAR(36) REFERENCES "grns" ("id") ON DELETE CASCADE,
    "location_id" VARCHAR(40) NOT NULL REFERENCES "locations" ("id") ON DELETE CASCADE,
    "submitted_by_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    "supplier_id" VARCHAR(40) NOT NULL REFERENCES "suppliers" ("id") ON DELETE CASCADE
);
        CREATE TABLE IF NOT EXISTS "purchase_return_lines" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "qty" VARCHAR(40) NOT NULL,
    "unit_price" VARCHAR(40) NOT NULL,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE,
    "purchase_return_id" CHAR(36) NOT NULL REFERENCES "purchase_returns" ("id") ON DELETE CASCADE
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "purchase_returns";
        DROP TABLE IF EXISTS "purchase_return_lines";"""


MODELS_STATE = (
    "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"
    "4fui8ZwQ=="
)
