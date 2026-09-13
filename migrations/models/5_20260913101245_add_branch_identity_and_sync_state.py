from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "branch_identity" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "branch_id" VARCHAR(60) NOT NULL,
    "code" VARCHAR(20) NOT NULL,
    "name" VARCHAR(140) NOT NULL,
    "address" VARCHAR(255),
    "city" VARCHAR(120),
    "phone" VARCHAR(40),
    "timezone" VARCHAR(60) NOT NULL,
    "cloud_url" VARCHAR(255) NOT NULL,
    "sync_secret" VARCHAR(200) NOT NULL,
    "verified_at" TIMESTAMP NOT NULL,
    "created_at" TIMESTAMP NOT NULL
);
        CREATE TABLE IF NOT EXISTS "sync_state" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "last_attempt_at" TIMESTAMP,
    "last_success_at" TIMESTAMP,
    "last_error" TEXT,
    "consecutive_failures" INT NOT NULL,
    "events_sent" INT NOT NULL,
    "running" INT NOT NULL
) /* The moving half: what the scheduler did last time, and what it is waiting on. */;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "sync_state";
        DROP TABLE IF EXISTS "branch_identity";"""


MODELS_STATE = (
    "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"
)
