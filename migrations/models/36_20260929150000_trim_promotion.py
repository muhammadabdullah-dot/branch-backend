# A campaign becomes what their campaigns actually are.
#
# Free units, a quantity limit and a spend limit come off, with the two running totals that existed only to enforce
# the limits. Not one of the old software's 11,364 campaigns uses any of them. They were carried for a day on the
# argument that they are part of what a campaign means and a shop may want them tomorrow, which is the same argument
# that put a salesperson on a bill and took it off again.
#
# The minimum quantity stays. It is unused there too (OnQty is 1.000 on all 11,364), but it is one number on a
# campaign rather than a mechanism, and "three for the price of two" is the first thing a shop asks for.
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "promotions" DROP COLUMN "bonus_qty";
        ALTER TABLE "promotions" DROP COLUMN "qty_limit";
        ALTER TABLE "promotions" DROP COLUMN "amount_limit";
        ALTER TABLE "promotions" DROP COLUMN "used_qty";
        ALTER TABLE "promotions" DROP COLUMN "used_amount";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "promotions" ADD COLUMN "bonus_qty" VARCHAR(40) NOT NULL DEFAULT 0;
        ALTER TABLE "promotions" ADD COLUMN "qty_limit" VARCHAR(40);
        ALTER TABLE "promotions" ADD COLUMN "amount_limit" VARCHAR(40);
        ALTER TABLE "promotions" ADD COLUMN "used_qty" VARCHAR(40) NOT NULL DEFAULT 0;
        ALTER TABLE "promotions" ADD COLUMN "used_amount" VARCHAR(40) NOT NULL DEFAULT 0;"""
