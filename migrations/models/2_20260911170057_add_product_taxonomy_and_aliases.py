from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "product_aliases" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "code" VARCHAR(60) NOT NULL UNIQUE,
    "remarks" VARCHAR(255),
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE
) /* The legacy 'AliasName' \/ alternate-barcode concept — one Product can have several of */;
        ALTER TABLE "products" ADD "category" VARCHAR(80);
        ALTER TABLE "products" ADD "rpp" VARCHAR(40);
        ALTER TABLE "products" ADD "subclass" VARCHAR(80);
        ALTER TABLE "products" ADD "item_class" VARCHAR(80);
        ALTER TABLE "products" ADD "active" INT NOT NULL DEFAULT 1;
        ALTER TABLE "products" ADD "department" VARCHAR(80);
        ALTER TABLE "products" ADD "brand" VARCHAR(120);
        ALTER TABLE "products" ADD "manufacturer" VARCHAR(120);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "products" DROP COLUMN "category";
        ALTER TABLE "products" DROP COLUMN "rpp";
        ALTER TABLE "products" DROP COLUMN "subclass";
        ALTER TABLE "products" DROP COLUMN "item_class";
        ALTER TABLE "products" DROP COLUMN "active";
        ALTER TABLE "products" DROP COLUMN "department";
        ALTER TABLE "products" DROP COLUMN "brand";
        ALTER TABLE "products" DROP COLUMN "manufacturer";
        DROP TABLE IF EXISTS "product_aliases";"""


MODELS_STATE = (
    "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"
    "+j/M/vp/OKQgYw=="
)
