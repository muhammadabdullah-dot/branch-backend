from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# Hand-corrected. Aerich emitted `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY` for products.parent_id,
# which SQLite doesn't support and would abort the migration. SQLite accepts the reference inline on
# ADD COLUMN for a nullable column, so the constraint moved there. Existing Items get parent_id NULL.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "product_price_changes" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "old_price" VARCHAR(40) NOT NULL,
    "new_price" VARCHAR(40) NOT NULL,
    "old_rpp" VARCHAR(40),
    "new_rpp" VARCHAR(40),
    "source" VARCHAR(20) NOT NULL,
    "at" TIMESTAMP NOT NULL,
    "changed_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE SET NULL,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE
) /* Every change to an Item's sale or retail price, and where it came from. Labels reads this to */;
        CREATE TABLE IF NOT EXISTS "product_suppliers" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "priority" INT NOT NULL,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE,
    "supplier_id" VARCHAR(40) NOT NULL REFERENCES "suppliers" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_product_sup_product_5546c9" UNIQUE ("product_id", "supplier_id")
) /* Who supplies this Item, in order of preference — the legacy Item form's Supplier grid. */;
        ALTER TABLE "products" ADD "parent_qty" VARCHAR(40);
        ALTER TABLE "products" ADD "picture" VARCHAR(160);
        ALTER TABLE "products" ADD "variant" VARCHAR(60);
        ALTER TABLE "products" ADD "remarks" VARCHAR(255);
        ALTER TABLE "products" ADD "parent_id" VARCHAR(40) REFERENCES "products" ("id") ON DELETE SET NULL;
        ALTER TABLE "products" ADD "disc_flat" VARCHAR(40) NOT NULL DEFAULT 0;
        ALTER TABLE "products" ADD "origin" VARCHAR(10);
        ALTER TABLE "products" ADD "disc_percent" VARCHAR(40) NOT NULL DEFAULT 0;
        ALTER TABLE "products" ADD "lock_disc" INT NOT NULL DEFAULT 0;
        ALTER TABLE "product_aliases" ADD "disc_flat" VARCHAR(40) NOT NULL DEFAULT 0;
        ALTER TABLE "product_aliases" ADD "disc_percent" VARCHAR(40) NOT NULL DEFAULT 0;
        ALTER TABLE "product_aliases" ADD "qty" VARCHAR(40) NOT NULL DEFAULT 1;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "products" DROP COLUMN "parent_qty";
        ALTER TABLE "products" DROP COLUMN "picture";
        ALTER TABLE "products" DROP COLUMN "variant";
        ALTER TABLE "products" DROP COLUMN "remarks";
        ALTER TABLE "products" DROP COLUMN "parent_id";
        ALTER TABLE "products" DROP COLUMN "disc_flat";
        ALTER TABLE "products" DROP COLUMN "origin";
        ALTER TABLE "products" DROP COLUMN "disc_percent";
        ALTER TABLE "products" DROP COLUMN "lock_disc";
        ALTER TABLE "product_aliases" DROP COLUMN "disc_flat";
        ALTER TABLE "product_aliases" DROP COLUMN "disc_percent";
        ALTER TABLE "product_aliases" DROP COLUMN "qty";
        DROP TABLE IF EXISTS "product_price_changes";
        DROP TABLE IF EXISTS "product_suppliers";"""


MODELS_STATE = (
    "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"
    "nQs3bQ/M//A9qjw3o="
)
