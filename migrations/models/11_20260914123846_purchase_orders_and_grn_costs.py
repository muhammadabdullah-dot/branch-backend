from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Hand-corrected. Aerich emitted `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY` for grns.purchase_order_id,
# which SQLite doesn't support. The reference moved inline onto ADD COLUMN (allowed for a nullable
# column). Existing GRNs get purchase_order_id NULL, flat_disc and misc 0, and no price change.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "purchase_orders" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "po_number" VARCHAR(20) NOT NULL UNIQUE,
    "status" VARCHAR(20) NOT NULL,
    "expected_at" TIMESTAMP,
    "notes" VARCHAR(255),
    "created_at" TIMESTAMP NOT NULL,
    "approved_at" TIMESTAMP,
    "closed_at" TIMESTAMP,
    "approved_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE CASCADE,
    "created_by_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    "location_id" VARCHAR(40) NOT NULL REFERENCES "locations" ("id") ON DELETE CASCADE,
    "supplier_id" VARCHAR(40) NOT NULL REFERENCES "suppliers" ("id") ON DELETE CASCADE
) /* What the branch has asked a supplier for. Receiving fills a GRN from an approved one and ticks */;
        CREATE TABLE IF NOT EXISTS "purchase_order_lines" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "qty" VARCHAR(40) NOT NULL,
    "unit_price" VARCHAR(40) NOT NULL,
    "disc_percent" VARCHAR(40) NOT NULL,
    "received_qty" VARCHAR(40) NOT NULL,
    "position" INT NOT NULL,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE,
    "purchase_order_id" CHAR(36) NOT NULL REFERENCES "purchase_orders" ("id") ON DELETE CASCADE
);
        ALTER TABLE "grns" ADD "purchase_order_id" CHAR(36) REFERENCES "purchase_orders" ("id") ON DELETE SET NULL;
        ALTER TABLE "grn_lines" ADD "new_sale_price" VARCHAR(40);
        ALTER TABLE "grn_lines" ADD "new_retail_price" VARCHAR(40);
        ALTER TABLE "grn_lines" ADD "flat_disc" VARCHAR(40) NOT NULL DEFAULT 0;
        ALTER TABLE "grn_lines" ADD "misc" VARCHAR(40) NOT NULL DEFAULT 0;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "grns" DROP COLUMN "purchase_order_id";
        ALTER TABLE "grn_lines" DROP COLUMN "new_sale_price";
        ALTER TABLE "grn_lines" DROP COLUMN "new_retail_price";
        ALTER TABLE "grn_lines" DROP COLUMN "flat_disc";
        ALTER TABLE "grn_lines" DROP COLUMN "misc";
        DROP TABLE IF EXISTS "purchase_orders";
        DROP TABLE IF EXISTS "purchase_order_lines";"""


MODELS_STATE = (
    "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"
    "WZ6ttaZSC2kdVnvUZWz9q19ff/B4pZOaM="
)
