from models import db, DocumentModel
from datetime import datetime


class InventoryItem(DocumentModel):
    """Inventory Item model for parts and supplies."""
    __collection__ = 'inventory_items'

    def __init__(self, **kwargs):
        self.name = kwargs.pop('name', None)
        self.category = kwargs.pop('category', None)
        self.sku = kwargs.pop('sku', None)
        self.description = kwargs.pop('description', None)
        self.stock = kwargs.pop('stock', 0)
        self.price = kwargs.pop('price', 0.0)
        self.cost = kwargs.pop('cost', None)
        self.reorder_level = kwargs.pop('reorder_level', 10)
        self.supplier = kwargs.pop('supplier', None)
        self.location = kwargs.pop('location', None)
        self.lead_time_days = kwargs.pop('lead_time_days', 7)
        self.safety_stock_percentage = kwargs.pop('safety_stock_percentage', 0.5)
        self.is_active = kwargs.pop('is_active', True)
        # Soft delete
        self.is_deleted = kwargs.pop('is_deleted', False)
        self.deleted_at = kwargs.pop('deleted_at', None)
        self.deleted_reason = kwargs.pop('deleted_reason', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        super().__init__(**kwargs)

    def deduct_stock(self, quantity):
        if self.stock >= quantity:
            self.stock -= quantity
            return True
        return False

    def add_stock(self, quantity):
        self.stock += quantity

    @property
    def needs_reorder(self):
        return self.stock <= self.reorder_level

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'category': self.category,
            'sku': self.sku,
            'description': self.description,
            'stock': self.stock,
            'price': self.price,
            'cost': self.cost,
            'reorder_level': self.reorder_level,
            'supplier': self.supplier,
            'location': self.location,
            'lead_time_days': self.lead_time_days,
            'safety_stock_percentage': self.safety_stock_percentage,
            'is_active': self.is_active,
            'needs_reorder': self.needs_reorder
        }
