"""
Enhanced Models - New Models Only (No Duplicates), Firestore-backed.
"""
from models import db, DocumentModel
from datetime import datetime

from models.service import Service
from models.inventory import InventoryItem
from models.user import User


class CarBrand(DocumentModel):
    """Car brand master data."""
    __collection__ = 'car_brands'

    def __init__(self, **kwargs):
        self.name = kwargs.pop('name', None)
        self.country = kwargs.pop('country', None)
        self.logo_url = kwargs.pop('logo_url', None)
        self.description = kwargs.pop('description', None)
        self.is_active = kwargs.pop('is_active', True)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'country': self.country,
            'logo_url': self.logo_url,
            'description': self.description,
            'is_active': self.is_active
        }


class VehicleType(DocumentModel):
    """Vehicle type/powertrain master data."""
    __collection__ = 'vehicle_types'

    def __init__(self, **kwargs):
        self.type_name = kwargs.pop('type_name', None)
        self.description = kwargs.pop('description', None)
        self.fuel_type = kwargs.pop('fuel_type', None)
        self.requires_oil_change = kwargs.pop('requires_oil_change', True)
        self.requires_transmission_service = kwargs.pop('requires_transmission_service', True)
        self.requires_exhaust_inspection = kwargs.pop('requires_exhaust_inspection', True)
        self.requires_battery_inspection = kwargs.pop('requires_battery_inspection', False)
        self.requires_coolant_flush = kwargs.pop('requires_coolant_flush', True)
        self.is_active = kwargs.pop('is_active', True)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'type_name': self.type_name,
            'description': self.description,
            'fuel_type': self.fuel_type,
            'requires_oil_change': self.requires_oil_change,
            'requires_transmission_service': self.requires_transmission_service,
            'requires_exhaust_inspection': self.requires_exhaust_inspection,
            'requires_battery_inspection': self.requires_battery_inspection,
            'requires_coolant_flush': self.requires_coolant_flush,
            'is_active': self.is_active
        }


class ServiceVehicleTypeCompatibility(DocumentModel):
    """Compatibility matrix: which services apply to which vehicle types."""
    __collection__ = 'service_vehicle_type_compatibility'

    def __init__(self, **kwargs):
        self.service_id = kwargs.pop('service_id', None)
        self.vehicle_type_id = kwargs.pop('vehicle_type_id', None)
        self.is_applicable = kwargs.pop('is_applicable', True)
        self.notes = kwargs.pop('notes', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'service_id': self.service_id,
            'vehicle_type_id': self.vehicle_type_id,
            'is_applicable': self.is_applicable,
            'notes': self.notes
        }


def notify_low_stock(item):
    """SMS every active admin/staff account with a phone number, and push
    to every one with a registered device (Capacitor app), that an item
    just crossed below its reorder level. Never raises -- a notification
    failure must not block the stock deduction that triggered it (same
    "log a warning, keep going" contract as utils/audit.py's log_audit()).
    """
    try:
        from flask import current_app
        from utils.sms_utils import send_sms
        from utils.push_utils import send_push, prune_invalid_tokens

        recipients = [
            u for u in User.query.filter_by(is_active=True).all()
            if u.role in ('admin', 'staff')
        ]
        if not recipients:
            current_app.logger.warning('notify_low_stock: item %s is low but there are no active admin/staff accounts', item.id)
            return

        message = (
            f"Automan Car Care: LOW STOCK ALERT - '{item.name}' (SKU: {item.sku or 'N/A'}) "
            f"is at {item.stock} units, at or below its reorder level of {item.reorder_level}. "
            f"Please restock soon."
        )
        for user in recipients:
            if user.contact_number:
                try:
                    send_sms(user.contact_number, message)
                except Exception as e:
                    current_app.logger.warning('notify_low_stock: failed to SMS %s for item %s: %s', user.id, item.id, e)
            if user.fcm_tokens:
                result = send_push(
                    user.fcm_tokens,
                    title='Low Stock Alert',
                    body=f"'{item.name}' is at {item.stock} units (reorder level: {item.reorder_level}).",
                    data={'type': 'low_stock', 'item_id': item.id},
                )
                prune_invalid_tokens(user, result.get('invalid_tokens'))
    except Exception as e:
        try:
            from flask import current_app
            current_app.logger.warning('notify_low_stock: failed for item %s: %s', getattr(item, 'id', None), e)
        except Exception:
            pass


class InventoryMovement(DocumentModel):
    """Inventory Movement Log - tracks all inventory IN/OUT/RETURN/ADJUSTMENT."""
    __collection__ = 'inventory_movements'

    def __init__(self, **kwargs):
        self.movement_number = kwargs.pop('movement_number', None)
        self.inventory_item_id = kwargs.pop('inventory_item_id', None)
        self.movement_type = kwargs.pop('movement_type', None)
        self.quantity = kwargs.pop('quantity', None)
        self.unit_price = kwargs.pop('unit_price', None)
        self.reference_type = kwargs.pop('reference_type', None)
        self.reference_id = kwargs.pop('reference_id', None)
        self.purchase_order_number = kwargs.pop('purchase_order_number', None)
        self.reason = kwargs.pop('reason', None)
        self.notes = kwargs.pop('notes', None)
        self.status = kwargs.pop('status', 'Completed')
        self.is_cancelled = kwargs.pop('is_cancelled', False)
        self.cancellation_reason = kwargs.pop('cancellation_reason', None)
        self.stock_before = kwargs.pop('stock_before', None)
        self.stock_after = kwargs.pop('stock_after', None)
        self.recorded_by_user_id = kwargs.pop('recorded_by_user_id', None)
        self.verified_by_user_id = kwargs.pop('verified_by_user_id', None)
        self.verified_at = kwargs.pop('verified_at', None)
        self.movement_date = kwargs.pop('movement_date', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        super().__init__(**kwargs)

    @staticmethod
    def generate_movement_number(movement_type_prefix):
        prefix_map = {'OUT': 'OUT', 'IN': 'IN', 'RET': 'RET', 'ADJ': 'ADJ', 'WAST': 'WAST'}
        prefix = prefix_map.get(movement_type_prefix, 'MOV')
        date_str = datetime.utcnow().strftime('%Y%m%d')
        return f"MOV-{prefix}-{date_str}-{uuid.uuid4().hex[:4].upper()}"

    @classmethod
    def record_usage(cls, inventory_item_id, quantity, reference_type=None,
                      reference_id=None, reason=None, user_id=None):
        """Deduct stock for a used item and log the OUT movement. Raises ValueError on insufficient stock."""
        item = InventoryItem.get(inventory_item_id)
        if not item:
            raise ValueError(f'Inventory item {inventory_item_id} not found')
        if (item.stock or 0) < quantity:
            raise ValueError(
                f'Insufficient stock for {item.name}. Available: {item.stock}, requested: {quantity}'
            )
        stock_before = item.stock
        item.stock -= quantity
        item.save()

        movement = cls(
            movement_number=cls.generate_movement_number('OUT'),
            inventory_item_id=inventory_item_id,
            movement_type='OUT',
            quantity=quantity,
            unit_price=item.cost or 0,
            reference_type=reference_type,
            reference_id=reference_id,
            reason=reason,
            recorded_by_user_id=user_id,
            stock_before=stock_before,
            stock_after=item.stock,
            movement_date=datetime.utcnow(),
        )
        movement.save()

        # This is the single choke point for every stock-decreasing path
        # (job-order parts release, manual "deduct" in inventory management)
        # -- so it's also the right place to catch an item newly crossing
        # its reorder threshold. Only fires on the actual crossing (was
        # above the line, now at/below it), not on every subsequent
        # deduction while it's already low, so restocking then using a
        # couple more units doesn't re-spam the same alert.
        reorder_level = item.reorder_level or 0
        if stock_before > reorder_level and item.stock <= reorder_level:
            notify_low_stock(item)

        return movement

    @classmethod
    def record_receipt(cls, inventory_item_id, quantity, unit_price=None,
                        purchase_order_num=None, reason=None, user_id=None):
        """Add stock received and log the IN movement."""
        item = InventoryItem.get(inventory_item_id)
        if not item:
            raise ValueError(f'Inventory item {inventory_item_id} not found')
        stock_before = item.stock
        item.stock = (item.stock or 0) + quantity
        item.save()

        movement = cls(
            movement_number=cls.generate_movement_number('IN'),
            inventory_item_id=inventory_item_id,
            movement_type='IN',
            quantity=quantity,
            unit_price=unit_price if unit_price is not None else (item.cost or 0),
            reference_type='purchase',
            purchase_order_number=purchase_order_num,
            reason=reason,
            recorded_by_user_id=user_id,
            stock_before=stock_before,
            stock_after=item.stock,
            movement_date=datetime.utcnow(),
        )
        movement.save()
        return movement

    def to_dict(self):
        return {
            'id': self.id,
            'movement_number': self.movement_number,
            'inventory_item_id': self.inventory_item_id,
            'movement_type': self.movement_type,
            'quantity': self.quantity,
            'unit_price': self.unit_price,
            'reference_type': self.reference_type,
            'reference_id': self.reference_id,
            'reason': self.reason,
            'status': self.status,
            'stock_before': self.stock_before,
            'stock_after': self.stock_after,
            'movement_date': self.movement_date.isoformat() if hasattr(self.movement_date, 'isoformat') else self.movement_date,
            'created_at': self.created_at.isoformat() if hasattr(self.created_at, 'isoformat') else self.created_at
        }


import uuid  # noqa: E402
