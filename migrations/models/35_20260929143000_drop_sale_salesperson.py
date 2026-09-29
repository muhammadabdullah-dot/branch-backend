# Taking the salesperson off a bill again, one day after putting it on.
#
# It was built because the old software has it. What that missed is that D.Marina already records who made the bill:
# the cashier is on every one of them, from whoever is signed in. A second name for "who is credited with the sale"
# only earns its place where the person who helps the customer is not the person who rings it up, and this shop does
# not work that way.
#
# Their own data agrees, which is why this is a cheap mistake rather than an expensive one: they credit somebody on
# 2,347 bills of 1,036,271, about one in 440, and their commission percentage is zero on all nine of their
# salespeople with all three commission tables empty. Nobody was ever paid on it.
#
# Migration 33 added the column and this drops it. The pair is left in the history rather than tidied away, because a
# database somewhere has already run 33 and the honest record is that we added it and then decided against it.
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sale_records" DROP COLUMN "salesperson";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sale_records" ADD COLUMN "salesperson" VARCHAR(80);"""
