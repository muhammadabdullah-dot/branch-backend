from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "known_branches" (
    "code" VARCHAR(20) NOT NULL PRIMARY KEY,
    "name" VARCHAR(140) NOT NULL,
    "city" VARCHAR(120),
    "updated_at" TIMESTAMP NOT NULL
) /* The other branches this one can send stock to, as head office last listed them. */;
        ALTER TABLE "roles" ADD "managed_by_head_office" INT NOT NULL DEFAULT 0;
        ALTER TABLE "roles" ADD "rolled_out_resources" JSON;
        ALTER TABLE "sync_state" ADD "pull_cursor" INT NOT NULL DEFAULT 0;
        ALTER TABLE "sync_state" ADD "last_pull_error" TEXT;
        ALTER TABLE "sync_state" ADD "pending_acks" JSON NOT NULL DEFAULT '[]';
        ALTER TABLE "sync_state" ADD "last_pull_at" TIMESTAMP;
        ALTER TABLE "transfers" ADD "counterparty_code" VARCHAR(20);
        ALTER TABLE "transfers" ADD "updated_at" TIMESTAMP NOT NULL DEFAULT '2026-09-14 00:00:00+00:00';
        ALTER TABLE "transfers" ADD "received_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE SET NULL;
        ALTER TABLE "transfers" ADD "received_by_name" VARCHAR(120);
        ALTER TABLE "transfers" ADD "origin" VARCHAR(10) NOT NULL DEFAULT 'local';
        ALTER TABLE "transfers" ADD "number" VARCHAR(30);
        ALTER TABLE "transfers" ADD "dispatched_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE SET NULL;
        ALTER TABLE "transfers" ADD "notes" VARCHAR(255);
        ALTER TABLE "transfers" ADD "direction" VARCHAR(10) NOT NULL DEFAULT 'inbound';
        ALTER TABLE "transfers" ADD "location_id" VARCHAR(40) REFERENCES "locations" ("id") ON DELETE SET NULL;
        ALTER TABLE "transfer_lines" ADD "sku" VARCHAR(60);
        ALTER TABLE "users" ADD "rev" INT NOT NULL DEFAULT 1;
        ALTER TABLE "users" ADD "updated_at" TIMESTAMP NOT NULL DEFAULT '2026-09-14 00:00:00+00:00';
        UPDATE "users" SET "updated_at" = "created_at";
        UPDATE "transfers" SET "updated_at" = COALESCE("received_at", "dispatched_at", "requested_at");
        UPDATE "transfer_lines" SET "sku" = (SELECT "sku" FROM "products" WHERE "products"."id" = "transfer_lines"."product_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "roles" DROP COLUMN "managed_by_head_office";
        ALTER TABLE "roles" DROP COLUMN "rolled_out_resources";
        ALTER TABLE "sync_state" DROP COLUMN "pull_cursor";
        ALTER TABLE "sync_state" DROP COLUMN "last_pull_error";
        ALTER TABLE "sync_state" DROP COLUMN "pending_acks";
        ALTER TABLE "sync_state" DROP COLUMN "last_pull_at";
        ALTER TABLE "transfers" DROP COLUMN "counterparty_code";
        ALTER TABLE "transfers" DROP COLUMN "updated_at";
        ALTER TABLE "transfers" DROP COLUMN "received_by_id";
        ALTER TABLE "transfers" DROP COLUMN "received_by_name";
        ALTER TABLE "transfers" DROP COLUMN "origin";
        ALTER TABLE "transfers" DROP COLUMN "number";
        ALTER TABLE "transfers" DROP COLUMN "dispatched_by_id";
        ALTER TABLE "transfers" DROP COLUMN "notes";
        ALTER TABLE "transfers" DROP COLUMN "direction";
        ALTER TABLE "transfers" DROP COLUMN "location_id";
        ALTER TABLE "transfer_lines" DROP COLUMN "sku";
        ALTER TABLE "users" DROP COLUMN "rev";
        ALTER TABLE "users" DROP COLUMN "updated_at";
        DROP TABLE IF EXISTS "known_branches";"""


MODELS_STATE = (
    "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"
    "k/QLEVQMFSUBOo/gAlU+GQL7Pf/z9rfeLY"
)
