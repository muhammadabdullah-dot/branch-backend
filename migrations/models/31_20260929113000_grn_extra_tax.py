# A further tax on a goods receipt line, over and above the GST.
#
# The old software's EGstPerc2 and EGstPerc3, which its own shops used on 172,832 of their 282,522 purchase lines: far
# too common to drop when their history is imported. One rate here rather than two of theirs, because what the shop
# owes is the sum and what an invoice shows is a line; a delivery that really carries two separate further taxes adds
# them, and the reconciliation reports the difference rather than hiding it.
#
# Defaults to zero, so every delivery already received reads exactly as it did.
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "grn_lines" ADD COLUMN "extra_tax_rate" VARCHAR(40) NOT NULL DEFAULT 0;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "grn_lines" DROP COLUMN "extra_tax_rate";"""
