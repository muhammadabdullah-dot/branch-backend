"""Who this branch is — written once, at verification, and never again.

Everything else in this database is transactional: sales happen, stock moves, users come and go.
This row is the opposite. It is the answer to "which branch am I", it arrives from the Cloud at
the end of a successful handshake, and from that moment it is read-only. No route updates it, no
service mutates it, and the only way it changes is an operator deliberately clearing the branch's
pairing on the Cloud and re-verifying.

That immutability is not neatness. `code` and `sync_secret` are the credentials this server
presents on every sync. A screen that let someone edit the branch code would let someone silently
point a shop's sales at a different branch's figures, and nothing downstream would ever notice.

**Identity is separate from sync bookkeeping on purpose.** "Who am I" never changes; "when did I
last reach the Cloud" changes every two hours. Keeping them in one row would mean writing to the
identity row constantly, and an immutability rule nobody can see being enforced is an immutability
rule that quietly stops being true. `SyncState` below holds everything that moves.
"""
from tortoise import fields, models

# There is exactly one branch per branch database, so there is exactly one identity row. Pinning
# the primary key rather than trusting "the first row" makes a second one impossible at the
# storage layer instead of merely unlikely at the service layer.
IDENTITY_PK = 1


class BranchIdentity(models.Model):
    id = fields.IntField(pk=True)

    # --- Handed down by the Cloud at verification -------------------------------------------
    branch_id = fields.CharField(max_length=60)
    code = fields.CharField(max_length=20)
    name = fields.CharField(max_length=140)
    address = fields.CharField(max_length=255, null=True)
    city = fields.CharField(max_length=120, null=True)
    phone = fields.CharField(max_length=40, null=True)
    timezone = fields.CharField(max_length=60, default="Asia/Karachi")

    # --- The credentials every sync presents --------------------------------------------------
    # Where we sync to, and what proves we are us. The secret is stored in the clear because this
    # server must be able to *present* it, not merely check it — a hash would be useless here.
    # Protecting it is a matter of protecting branch.db itself, which is also where every sale in
    # the shop lives, so the file already needs that level of care.
    cloud_url = fields.CharField(max_length=255)
    sync_secret = fields.CharField(max_length=200)

    verified_at = fields.DatetimeField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "branch_identity"

    def __str__(self) -> str:
        return f"{self.code}: {self.name}"


class SyncState(models.Model):
    """The moving half: what the scheduler did last time, and what it is waiting on.

    Also a singleton. Failures are counted rather than just recorded because the difference between
    "one missed tick" and "offline since Tuesday" is the difference between normal operation in a
    Pakistani retail branch and something a person needs to go and look at.
    """

    id = fields.IntField(pk=True)
    last_attempt_at = fields.DatetimeField(null=True)
    last_success_at = fields.DatetimeField(null=True)
    last_error = fields.TextField(null=True)
    consecutive_failures = fields.IntField(default=0)
    # Cumulative, across the life of the branch — the number an operator reads as "is this thing
    # actually doing anything".
    events_sent = fields.IntField(default=0)
    running = fields.BooleanField(default=False)

    # --- Downstream: what head office sends here ---------------------------------------------
    # The last head-office message this branch applied. Pulls ask for everything after it.
    pull_cursor = fields.IntField(default=0)
    last_pull_at = fields.DatetimeField(null=True)
    last_pull_error = fields.TextField(null=True)
    # Apply results not yet reported back because the link dropped after applying; sent with the next pull.
    pending_acks = fields.JSONField(default=list)

    # --- Stock head office already has ------------------------------------------------------------
    # The last stock movement (by row) and the moment head office's item-level stock was brought up to
    # date. Between full pushes, only items that moved after this go up.
    stock_mark = fields.IntField(default=0)
    stock_mark_at = fields.DatetimeField(null=True)

    class Meta:
        table = "sync_state"
