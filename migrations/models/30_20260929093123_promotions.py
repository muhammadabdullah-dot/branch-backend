# Campaigns from head office, and the line that remembers which one gave it its discount.
#
# Hand-corrected, as migrations 6 and 20 were: aerich writes `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY`, which
# SQLite has no syntax for. SQLite does accept a REFERENCES clause on an added column as long as that column is
# nullable, which this one is, so the link is written inline instead. The downgrade drops the column rather than the
# constraint, for the same reason.
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "promotions" (
    "id" VARCHAR(60) NOT NULL PRIMARY KEY,
    "code" VARCHAR(20) NOT NULL UNIQUE,
    "name" VARCHAR(160) NOT NULL,
    "starts_on" DATE NOT NULL,
    "ends_on" DATE NOT NULL,
    "kind" VARCHAR(10) NOT NULL,
    "disc_percent" VARCHAR(40) NOT NULL,
    "disc_flat" VARCHAR(40) NOT NULL,
    "promo_price" VARCHAR(40),
    "min_qty" VARCHAR(40) NOT NULL,
    "bonus_qty" VARCHAR(40) NOT NULL,
    "qty_limit" VARCHAR(40),
    "amount_limit" VARCHAR(40),
    "used_qty" VARCHAR(40) NOT NULL,
    "used_amount" VARCHAR(40) NOT NULL,
    "active" INT NOT NULL,
    "remarks" VARCHAR(255),
    "rev" INT NOT NULL,
    "created_at" TIMESTAMP NOT NULL,
    "updated_at" TIMESTAMP NOT NULL,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE
);
        CREATE INDEX IF NOT EXISTS "idx_promotions_product_days" ON "promotions" ("product_id", "starts_on", "ends_on");
        ALTER TABLE "sale_lines" ADD "promotion_id" VARCHAR(60) REFERENCES "promotions" ("id") ON DELETE SET NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sale_lines" DROP COLUMN "promotion_id";
        DROP TABLE IF EXISTS "promotions";"""
