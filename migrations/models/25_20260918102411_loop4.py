from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "login_sessions" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "device_id" VARCHAR(80),
    "device_name" VARCHAR(120),
    "ip" VARCHAR(60),
    "one_login" INT NOT NULL,
    "started_at" TIMESTAMP NOT NULL,
    "last_seen_at" TIMESTAMP NOT NULL,
    "expires_at" TIMESTAMP NOT NULL,
    "ended_at" TIMESTAMP,
    "end_kind" VARCHAR(20),
    "end_reason" VARCHAR(200),
    "ended_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE SET NULL,
    "user_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_login_sessi_ended_a_b4ddea" ON "login_sessions" ("ended_at");
CREATE INDEX IF NOT EXISTS "idx_login_sessi_user_id_7a4fc9" ON "login_sessions" ("user_id", "ended_at");
        CREATE TABLE IF NOT EXISTS "scan_lines" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "bill_id" VARCHAR(80) NOT NULL,
    "line_key" INT NOT NULL,
    "device_id" VARCHAR(80),
    "how" VARCHAR(12) NOT NULL,
    "first_at" TIMESTAMP NOT NULL,
    "last_at" TIMESTAMP NOT NULL,
    "qty" VARCHAR(40) NOT NULL,
    "level" VARCHAR(8),
    "outcome" VARCHAR(12) NOT NULL,
    "ended_at" TIMESTAMP,
    "invoice_number" VARCHAR(30),
    "counter_id" CHAR(36) REFERENCES "sales_counters" ("id") ON DELETE SET NULL,
    "product_id" VARCHAR(40) NOT NULL REFERENCES "products" ("id") ON DELETE CASCADE,
    "till_session_id" CHAR(36) REFERENCES "till_sessions" ("id") ON DELETE SET NULL,
    "user_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_scan_lines_first_a_64e730" ON "scan_lines" ("first_at");
CREATE INDEX IF NOT EXISTS "idx_scan_lines_user_id_ebd418" ON "scan_lines" ("user_id", "first_at");
CREATE INDEX IF NOT EXISTS "idx_scan_lines_bill_id_444f68" ON "scan_lines" ("bill_id", "line_key");
        CREATE TABLE IF NOT EXISTS "scan_events" (
    "id" VARCHAR(40) NOT NULL PRIMARY KEY,
    "at" TIMESTAMP NOT NULL,
    "action" VARCHAR(12) NOT NULL,
    "qty" VARCHAR(40) NOT NULL,
    "level" VARCHAR(8),
    "line_id" CHAR(36) NOT NULL REFERENCES "scan_lines" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_scan_events_line_id_cc90e7" ON "scan_events" ("line_id");
        CREATE TABLE IF NOT EXISTS "user_devices" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "assigned_at" TIMESTAMP NOT NULL,
    "assigned_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE SET NULL,
    "device_id" VARCHAR(80) NOT NULL REFERENCES "devices" ("id") ON DELETE CASCADE,
    "user_id" CHAR(36) NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_user_device_user_id_0f1d5b" UNIQUE ("user_id", "device_id")
) /* A device one person may sign in on. Only read for people under the counter staff rule. */;
        ALTER TABLE "devices" ADD "registered_at" TIMESTAMP;
        ALTER TABLE "devices" ADD "registered_by_id" CHAR(36) REFERENCES "users" ("id") ON DELETE SET NULL;
        ALTER TABLE "products" ADD "box_price" VARCHAR(40);
        ALTER TABLE "products" ADD "piece_price" VARCHAR(40);
        ALTER TABLE "products" ADD "piece_unit" VARCHAR(20);
        ALTER TABLE "products" ADD "packs_per_box" INT;
        ALTER TABLE "products" ADD "pieces_per_strip" INT;
        ALTER TABLE "products" ADD "needs_details" INT NOT NULL DEFAULT 0;
        ALTER TABLE "products" ADD "pack_price" VARCHAR(40);
        ALTER TABLE "products" ADD "strip_price" VARCHAR(40);
        ALTER TABLE "products" ADD "pieces_per_unit" INT;
        ALTER TABLE "products" ADD "details_note" VARCHAR(200);
        ALTER TABLE "product_price_changes" ADD "field" VARCHAR(30);
        ALTER TABLE "product_price_changes" ADD "old_value" VARCHAR(40);
        ALTER TABLE "product_price_changes" ADD "reference" VARCHAR(120);
        ALTER TABLE "product_price_changes" ADD "new_value" VARCHAR(40);
        ALTER TABLE "return_records" ADD "exchange_sale_id" CHAR(36) REFERENCES "sale_records" ("id") ON DELETE SET NULL;
        ALTER TABLE "return_records" ADD "kind" VARCHAR(10);
        ALTER TABLE "return_records" ADD "number" VARCHAR(30);
        ALTER TABLE "sale_lines" ADD "sell_level" VARCHAR(8);
        ALTER TABLE "sale_lines" ADD "level_qty" VARCHAR(40);
        ALTER TABLE "sale_lines" ADD "level_price" VARCHAR(40);
        ALTER TABLE "sale_lines" ADD "return_of_invoice" VARCHAR(30);
        ALTER TABLE "sale_lines" ADD "level_detail" VARCHAR(30);
        ALTER TABLE "users" ADD "counter_login" INT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "devices" DROP COLUMN "registered_at";
        ALTER TABLE "devices" DROP COLUMN "registered_by_id";
        ALTER TABLE "products" DROP COLUMN "box_price";
        ALTER TABLE "products" DROP COLUMN "piece_price";
        ALTER TABLE "products" DROP COLUMN "piece_unit";
        ALTER TABLE "products" DROP COLUMN "packs_per_box";
        ALTER TABLE "products" DROP COLUMN "pieces_per_strip";
        ALTER TABLE "products" DROP COLUMN "needs_details";
        ALTER TABLE "products" DROP COLUMN "pack_price";
        ALTER TABLE "products" DROP COLUMN "strip_price";
        ALTER TABLE "products" DROP COLUMN "pieces_per_unit";
        ALTER TABLE "products" DROP COLUMN "details_note";
        ALTER TABLE "product_price_changes" DROP COLUMN "field";
        ALTER TABLE "product_price_changes" DROP COLUMN "old_value";
        ALTER TABLE "product_price_changes" DROP COLUMN "reference";
        ALTER TABLE "product_price_changes" DROP COLUMN "new_value";
        ALTER TABLE "return_records" DROP COLUMN "exchange_sale_id";
        ALTER TABLE "return_records" DROP COLUMN "kind";
        ALTER TABLE "return_records" DROP COLUMN "number";
        ALTER TABLE "sale_lines" DROP COLUMN "sell_level";
        ALTER TABLE "sale_lines" DROP COLUMN "level_qty";
        ALTER TABLE "sale_lines" DROP COLUMN "level_price";
        ALTER TABLE "sale_lines" DROP COLUMN "return_of_invoice";
        ALTER TABLE "sale_lines" DROP COLUMN "level_detail";
        ALTER TABLE "users" DROP COLUMN "counter_login";
        DROP TABLE IF EXISTS "user_devices";
        DROP TABLE IF EXISTS "scan_lines";
        DROP TABLE IF EXISTS "login_sessions";
        DROP TABLE IF EXISTS "scan_events";"""


MODELS_STATE = (
    "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"
    "/fXNu6ykICd4VyQqDHmZ/fv/A48d2Hk="
)
