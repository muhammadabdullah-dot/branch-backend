from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "loyalty_settings" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "enabled" INT NOT NULL,
    "rupees_per_point" VARCHAR(40) NOT NULL,
    "point_value" VARCHAR(40) NOT NULL,
    "min_redeem_points" INT NOT NULL,
    "max_redeem_percent" VARCHAR(40) NOT NULL,
    "updated_at" TIMESTAMP,
    "updated_by_name" VARCHAR(120),
    "updated_from" VARCHAR(10)
) /* How points are earned and what they're worth. Set here or at head office; the later change wins */;
        CREATE TABLE IF NOT EXISTS "members" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "code" VARCHAR(20) NOT NULL UNIQUE,
    "name" VARCHAR(120) NOT NULL,
    "phone" VARCHAR(20) NOT NULL UNIQUE,
    "joined_via" VARCHAR(20) NOT NULL,
    "joined_method" VARCHAR(20),
    "home_branch_code" VARCHAR(10) NOT NULL,
    "points_balance" INT NOT NULL,
    "active" INT NOT NULL,
    "created_by_name" VARCHAR(120),
    "created_at" TIMESTAMP NOT NULL,
    "updated_at" TIMESTAMP NOT NULL,
    "party_id" CHAR(36) REFERENCES "parties" ("id") ON DELETE SET NULL
);
        CREATE TABLE IF NOT EXISTS "loyalty_entries" (
    "id" CHAR(36) NOT NULL PRIMARY KEY,
    "kind" VARCHAR(10) NOT NULL,
    "points" INT NOT NULL,
    "invoice_number" VARCHAR(30),
    "branch_code" VARCHAR(10) NOT NULL,
    "note" VARCHAR(255),
    "by_name" VARCHAR(120),
    "at" TIMESTAMP NOT NULL,
    "member_id" CHAR(36) NOT NULL REFERENCES "members" ("id") ON DELETE CASCADE
) /* One movement of points. Its id travels with it to head office and on to other branches, so the */;
        ALTER TABLE "sale_records" ADD "points_redeemed" INT NOT NULL DEFAULT 0;
        ALTER TABLE "sale_records" ADD "member_id" CHAR(36) REFERENCES "members" ("id") ON DELETE SET NULL;
        ALTER TABLE "sale_tenders" ADD "reference" VARCHAR(40);
        ALTER TABLE "sale_tenders" ADD "account" VARCHAR(80);
        ALTER TABLE "sale_tenders" ADD "proof" VARCHAR(160);
        ALTER TABLE "sale_tenders" ADD "transaction_id" VARCHAR(60);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sale_records" DROP COLUMN "points_redeemed";
        ALTER TABLE "sale_records" DROP COLUMN "member_id";
        ALTER TABLE "sale_tenders" DROP COLUMN "reference";
        ALTER TABLE "sale_tenders" DROP COLUMN "account";
        ALTER TABLE "sale_tenders" DROP COLUMN "proof";
        ALTER TABLE "sale_tenders" DROP COLUMN "transaction_id";
        DROP TABLE IF EXISTS "loyalty_entries";
        DROP TABLE IF EXISTS "loyalty_settings";
        DROP TABLE IF EXISTS "members";"""


MODELS_STATE = (
    "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"
    "BOmrSDlBm7k7aAXJDZjY6iXnBK0SPOR3rL5HpUpNAlb5npsKR5RkD7SERPtyfyliHlA2Iz"
    "SKl2lpvrUT7F8FfhnYO1N2q14LZbLK41pvB5YErRWobwWqOH6oxQZcS+wv+Sh610f7SSZo"
    "lUvQkEpQ2W9TdWHQ3OCzYjvv7eNZ5du104tJGdvAXBS1UDdDvcCcpLnyGjBnxSzEmxUV8P"
    "QbXhXlbCHM5uomK7zKINsuQoUKEYWlpVsfIutIzBSa3J7+eYGFn61vaQjuvZI/71U97Ul1"
    "IY3VtWavbjLKenQN9+jqe3J39OAeWPq2RzhES0QH4pzQHo0voiq+xFVgx2h/xdWNdIBeS+"
    "wRZVXhKhgrxqoWkPOMOfgsFFpVH8qSJtY06aPARmy58rgRoX5tngph83bu7oNv43CYjRjg"
    "BfhEDdNAOCNi3hLuIaeek4WecreWMA/fHipvVEcyGnRnkyIYe0iv51E1wdgfoVs5rUrWPN"
    "h7UTg4/BaCkFuxEoqyln0yaGaCR8QcR1U9t3jXJWHLQhnyu17tiNt81TlR+56H/J7VZxlK"
    "cNJYukaaUFHU5gpp5Aql4CktT4ffUpQzQUvZN70lnKu9Z5utLS9poLqiGiR5NzRYrmu1jH"
    "SrK1WaySgg2kH0KlnbjuX3oXQVOjBXTdZa1VvHZUm7ihvZIC/Tj2WYyO7UjuXu8h5df726"
    "yvdjye2rHWBsdkeWqlOoBciZj95CvAHi8gZZA3AbVpMq27Ujoyll4FwROkZ18kUZTRFmDa"
    "ymFNPNzCZn9SYtvclcepN4CnUU12S4eQprq1YrDZ1Wyo1WfpPLmtI+m9og19X2OZREtva9"
    "kBVY6em1Bc5Z0cOk4reH2jYe3ZOhuzp/9Q7AgpjNeGi2cVO4OjAOsoTxcUK70UIoLK7NPU"
    "dtu9xu2+W+TAmcyOytMCZSc7jeiFANVK3tYLjtYFs19ssy2nNnuy1W8bgb282xEM+Me45f"
    "mSzdoNUWBQ1c0bYb0Li5Fi6HLeP9eUkb7x9KvD9ZkhsYbAuNKrzJ6P1V3z0dSfVdS5Y5RL"
    "IMZwFo+rcyIgbqAN04txq8MAq+DnwEt8k0IwO7dXx2vci2rzaBPdWbKao34SjntlflMdeJ"
    "0p6vJhytc7y5S1sWMRE+zoiUe8VsLMs1r3Zi4Tu2sklrvFQvzoYQlm3KCascbeHg+ZyznX"
    "H64i8FcXFwoSY1dO9KAOto29JFbJRrbM6JC47rYzrdlcCUxCy+qBkvogkNXWaF1q9dfaCa"
    "PWBNQC5xuuwXOCOa53a2yRnbRjfvIIpBm3OmGiHuCloM1m3U8NVUyAQOdj0Q7nAAZoPkER"
    "HpHA5bAOfE2ytiYzwAhGTuU1eW0p2aTMNUGiNgSeFN1U5nV355vnWPiZ/jqjyls86f2BG0"
    "9iynMa6uNWDdGOOGwzXlOKoVPgeu2rdX94/QwUtRir6sJjMUtZdDazSbWN90tgxqNcS2PK"
    "7NFLfi+t/Idju69wExGixRNA/CdIlwKH3GyT+jd45cH9wnpIJsAr1yGY3mEyczDz2Er1/j"
    "P56dvPkePYRnr0/fojnwH9RjHCPKZPQvFSo4OSq8gr3dtIKx97cIJ3WRg2Ahd+Ho75bFNy"
    "QW3+q9aJUOW8sYGPfsp0gppo76wjTZT1kxW2xGhwCFqfPM1WV9wFdyFnFNxOEbuOFWmGck"
    "LeoaqFuu0iFylVIDTLsaSknQFkMpfE9Kr9fDNCNi8+6aGV+pNbCfghxjZXxlFtTmXLv1F7"
    "0/YIf55W/EtbT5DSnb7q8sdH3gt+DBLEWg5KEoD2p0Uizi4Q5fjbdZeYbb84QumGKy6Dcu"
    "KksaaNt338EIz1JalEb5ibXQgRb5aN8EUt+wsQbN+DKX0pNK75DIS1nlu1n5TtDqQE38SC"
    "YyUUXGC+5GdTG/uoakK54DJ65fpSAmVxq1QrweMxhFsDZJsfLTrshRTF7gbgpgn3t3JzmK"
    "9XrfAngatGyr8GVEDNT0+slgn891EE6GG4ju6et2rRObeidWNPKjsrJk3p/vbq7r2vetRI"
    "oKH3El+jcKiBhxzsKkAlwFRk6rK/UHKLYCKGgKagLVH6CkKuzzMPv9/wCDPbQ0"
)
