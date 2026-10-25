from models import db, DocumentModel
from datetime import datetime

# See models/appointment.py's _UNSET for why: an inventory_item_id can point
# at a since-deleted item, and None can't double as both "not looked up yet"
# and "looked up, genuinely doesn't exist" without re-querying forever.
_UNSET = object()


class JobOrderItem(DocumentModel):
    """Job Order Item - connects job orders to inventory items."""
    __collection__ = 'job_order_items'

    def __init__(self, **kwargs):
        self.job_order_id = kwargs.pop('job_order_id', None)
        self.inventory_item_id = kwargs.pop('inventory_item_id', None)
        self.quantity_used = kwargs.pop('quantity_used', None)
        self.unit_price = kwargs.pop('unit_price', None)
        self.is_released = kwargs.pop('is_released', False)
        self.released_at = kwargs.pop('released_at', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self._inventory_item = kwargs.pop('inventory_item', _UNSET)
        super().__init__(**kwargs)

    @property
    def total_price(self):
        return (self.quantity_used or 0) * (self.unit_price or 0)

    @property
    def inventory_item(self):
        from models.inventory import InventoryItem
        if self._inventory_item is not _UNSET:
            return self._inventory_item
        self._inventory_item = InventoryItem.get(self.inventory_item_id) if self.inventory_item_id else None
        return self._inventory_item

    def to_dict(self):
        return {
            'id': self.id,
            'job_order_id': self.job_order_id,
            'inventory_item_id': self.inventory_item_id,
            'quantity_used': self.quantity_used,
            'unit_price': self.unit_price,
            'total_price': self.total_price,
            'is_released': self.is_released,
            'inventory_item': self.inventory_item.to_dict() if self.inventory_item else None
        }
