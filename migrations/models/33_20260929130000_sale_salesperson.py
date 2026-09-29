# Who on the floor is credited with a sale.
#
# The old software keeps a list of nine and credits one on a bill; five of them appear on bills, 2,347 of 1,036,271,
# so it is used and lightly. Its commission columns are zero on all nine and its three commission tables are empty,
# so no commission is carried.
#
# A name rather than a link to a user: the people it credits are shop floor staff who never sign in, and none of its
# nine is a till user. The names come from the shop's own list (services/masters_service.py ITEM_LISTS), which catches
# up from the bills themselves, so nothing has to be set up before the first bill can name somebody.
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sale_records" ADD COLUMN "salesperson" VARCHAR(80);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sale_records" DROP COLUMN "salesperson";"""
