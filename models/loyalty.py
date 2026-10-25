"""Customer loyalty and rewards.

Four collections:

- ``loyalty_accounts``   one per customer, keyed by the customer document id
  so a read is a single ``get`` rather than a field query.
- ``loyalty_transactions``  an append-only ledger. Never edited, never
  deleted -- a correction is a new transaction (see the ``reversal`` and
  ``adjustment`` types), which is what makes ``balance_after`` on every row
  independently verifiable. Document ids are deterministic
  (``earn_<sale_id>``/``reversal_<sale_id>``) which is what makes awarding
  idempotent without any extra bookkeeping.
- ``rewards``            the redeemable catalog.
- ``reward_redemptions`` one row per issued voucher.
"""
from models import db, DocumentModel
from datetime import datetime


# Transaction types. Kept as module constants rather than an enum so they
# serialize to plain strings in Firestore, matching how the rest of the
# codebase stores status values.
TX_EARN = 'earn'
TX_REDEEM = 'redeem'
TX_REVERSAL = 'reversal'
TX_ADJUSTMENT = 'adjustment'
TX_EXPIRE = 'expire'

VALID_TX_TYPES = {TX_EARN, TX_REDEEM, TX_REVERSAL, TX_ADJUSTMENT, TX_EXPIRE}

REWARD_DISCOUNT = 'discount'
REWARD_SERVICE = 'service'
REWARD_ITEM = 'item'
VALID_REWARD_TYPES = {REWARD_DISCOUNT, REWARD_SERVICE, REWARD_ITEM}

VOUCHER_ACTIVE = 'active'
VOUCHER_USED = 'used'
VOUCHER_EXPIRED = 'expired'
VOUCHER_CANCELLED = 'cancelled'

TIER_BRONZE = 'Bronze'
TIER_SILVER = 'Silver'
TIER_GOLD = 'Gold'


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


class LoyaltyAccount(DocumentModel):
    """Points balance and tier for one customer.

    The Firestore document id IS the customer id, so ``customer_id`` is
    stored as a field too (for queries/audit readability) but lookups go
    straight to ``get_db().collection(...).document(customer_id)``.
    """
    __collection__ = 'loyalty_accounts'

    def __init__(self, **kwargs):
        self.customer_id = kwargs.pop('customer_id', None) or kwargs.get('id')
        self.points_balance = kwargs.pop('points_balance', 0)
        self.lifetime_points = kwargs.pop('lifetime_points', 0)
        self.tier = kwargs.pop('tier', TIER_BRONZE)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'points_balance': self.points_balance,
            'lifetime_points': self.lifetime_points,
            'tier': self.tier,
            'created_at': _iso(self.created_at),
            'updated_at': _iso(self.updated_at),
        }


class LoyaltyTransaction(DocumentModel):
    """One row of a customer's points ledger. Append-only."""
    __collection__ = 'loyalty_transactions'

    def __init__(self, **kwargs):
        self.customer_id = kwargs.pop('customer_id', None)
        self.type = kwargs.pop('type', None)
        self.points = kwargs.pop('points', 0)  # signed: positive earns, negative spends
        self.balance_after = kwargs.pop('balance_after', 0)
        self.sale_id = kwargs.pop('sale_id', None)
        self.job_order_id = kwargs.pop('job_order_id', None)
        self.reward_id = kwargs.pop('reward_id', None)
        self.note = kwargs.pop('note', None)
        self.created_by = kwargs.pop('created_by', None)
        # Set on 'earn' rows when points expiry is enabled; a lazy-expiry
        # sweep on read looks for earns whose expires_at has passed. Stays
        # None on every other row.
        self.expires_at = kwargs.pop('expires_at', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'type': self.type,
            'points': self.points,
            'balance_after': self.balance_after,
            'sale_id': self.sale_id,
            'job_order_id': self.job_order_id,
            'reward_id': self.reward_id,
            'note': self.note,
            'created_at': _iso(self.created_at),
            'expires_at': _iso(self.expires_at),
        }


class Reward(DocumentModel):
    """A redeemable catalog item. Delete is a deactivation, never a hard delete."""
    __collection__ = 'rewards'

    def __init__(self, **kwargs):
        self.name = kwargs.pop('name', None)
        self.description = kwargs.pop('description', None)
        self.points_cost = kwargs.pop('points_cost', 0)
        self.reward_type = kwargs.pop('reward_type', REWARD_DISCOUNT)
        # For 'discount' this is the PHP amount off; for 'service'/'item' it is
        # a free-text description of what's delivered and staff enter the value
        # by hand. Null is a legitimate value for non-discount rewards.
        self.value = kwargs.pop('value', None)
        self.stock = kwargs.pop('stock', None)  # None = unlimited
        self.per_customer_limit = kwargs.pop('per_customer_limit', 0)  # 0 = unlimited
        self.is_active = kwargs.pop('is_active', True)
        self.image_url = kwargs.pop('image_url', None)
        # Soft delete (see models/customer.py)
        self.is_deleted = kwargs.pop('is_deleted', False)
        self.deleted_at = kwargs.pop('deleted_at', None)
        self.deleted_reason = kwargs.pop('deleted_reason', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        super().__init__(**kwargs)

    @property
    def is_in_stock(self):
        return self.stock is None or self.stock > 0

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'points_cost': self.points_cost,
            'reward_type': self.reward_type,
            'value': self.value,
            'stock': self.stock,
            'per_customer_limit': self.per_customer_limit,
            'is_active': self.is_active,
            'in_stock': self.is_in_stock,
            'image_url': self.image_url,
            'created_at': _iso(self.created_at),
            'updated_at': _iso(self.updated_at),
        }


class RewardRedemption(DocumentModel):
    """One issued voucher. Looked up by ``voucher_code`` (the document id)."""
    __collection__ = 'reward_redemptions'

    def __init__(self, **kwargs):
        self.customer_id = kwargs.pop('customer_id', None)
        self.reward_id = kwargs.pop('reward_id', None)
        self.reward_name = kwargs.pop('reward_name', None)
        self.voucher_code = kwargs.pop('voucher_code', None)
        self.points_spent = kwargs.pop('points_spent', 0)
        self.status = kwargs.pop('status', VOUCHER_ACTIVE)
        self.expires_at = kwargs.pop('expires_at', None)
        self.used_at = kwargs.pop('used_at', None)
        self.used_on_sale_id = kwargs.pop('used_on_sale_id', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'reward_id': self.reward_id,
            'reward_name': self.reward_name,
            'voucher_code': self.voucher_code,
            'points_spent': self.points_spent,
            'status': self.status,
            'expires_at': _iso(self.expires_at),
            'used_at': _iso(self.used_at),
            'used_on_sale_id': self.used_on_sale_id,
            'created_at': _iso(self.created_at),
        }
