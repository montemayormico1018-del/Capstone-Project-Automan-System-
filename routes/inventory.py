from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models import db
from models.inventory import InventoryItem
from models.job_order_item import JobOrderItem
from models.enhanced_models import InventoryMovement
from datetime import datetime, date
from utils.audit import log_audit

inventory_bp = Blueprint('inventory', __name__)


def _non_negative_number(value, field_name, required=True):
    """Coerce value to a number and reject anything negative or non-numeric.
    Returns (coerced_value, error_message_or_None) -- same shape as
    routes/job_orders.py's _capped_text() helper. adjust_stock() below never
    needed this (it always routes through InventoryMovement.record_usage/
    record_receipt, which already guard against negative results), but
    create/update let stock, price, cost, and reorder_level through
    completely unvalidated -- including straight to a negative number.

    required=False allows None through as-is (for cost, which is a
    genuinely optional/nullable field on the model); every other caller
    leaves it True since stock/price/reorder_level must never be None on a
    real item.
    """
    if value is None:
        if required:
            return None, f'{field_name} is required'
        return None, None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f'{field_name} must be a number'
    if number < 0:
        return None, f'{field_name} cannot be negative'
    return number, None


@inventory_bp.route('', methods=['GET'])
@jwt_required()
def get_inventory():
    """Get all inventory items."""
    category = request.args.get('category')
    low_stock_only = request.args.get('low_stock_only') == 'true'

    query = InventoryItem.query.filter_by(is_active=True)

    if category:
        query = query.filter_by(category=category)

    items = query.all()

    if low_stock_only:
        # Firestore can only compare a field against a literal in a query,
        # not one field against another (stock vs reorder_level) — filter
        # in Python instead, same avoidance pattern used elsewhere in this
        # data layer for cross-field comparisons.
        items = [i for i in items if i.stock <= i.reorder_level]

    return jsonify([item.to_dict() for item in items]), 200


@inventory_bp.route('/<item_id>', methods=['GET'])
@jwt_required()
def get_inventory_item(item_id):
    """Get a specific inventory item."""
    item = InventoryItem.query.get(item_id)
    if not item:
        return jsonify({'error': 'Inventory item not found'}), 404
    
    return jsonify(item.to_dict()), 200


@inventory_bp.route('', methods=['POST'])
@jwt_required()
def create_inventory_item():
    """Create a new inventory item."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role not in ['admin', 'staff']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    
    name = data.get('name')
    price = data.get('price')
    # Category is free text (with a datalist of existing ones as a nudge,
    # not an enforced list) — trim it server-side so stray leading/trailing
    # whitespace can't silently mint a "new" category that's really a
    # duplicate of an existing one (e.g. "oil" vs "oil ").
    category = (data.get('category') or '').strip() or None
    sku = data.get('sku')
    description = data.get('description')
    # .get(key, default) only falls back to the default when the KEY is
    # missing, not when it's explicitly sent as null -- guard both, since
    # stock/reorder_level must never end up None.
    stock = data.get('stock')
    if stock is None:
        stock = 0
    cost = data.get('cost')
    reorder_level = data.get('reorder_level')
    if reorder_level is None:
        reorder_level = 10
    supplier = data.get('supplier')
    location = data.get('location')

    if not name or price is None:
        return jsonify({'error': 'Name and price are required'}), 400

    price, err = _non_negative_number(price, 'Price')
    if err:
        return jsonify({'error': err}), 400
    stock, err = _non_negative_number(stock, 'Stock')
    if err:
        return jsonify({'error': err}), 400
    cost, err = _non_negative_number(cost, 'Cost', required=False)
    if err:
        return jsonify({'error': err}), 400
    reorder_level, err = _non_negative_number(reorder_level, 'Reorder level')
    if err:
        return jsonify({'error': err}), 400

    # Check for duplicate SKU
    if sku:
        existing = InventoryItem.query.filter_by(sku=sku).first()
        if existing:
            return jsonify({'error': 'SKU already exists'}), 400
    
    item = InventoryItem(
        name=name,
        price=price,
        category=category,
        sku=sku,
        description=description,
        stock=stock,
        cost=cost,
        reorder_level=reorder_level,
        supplier=supplier,
        location=location
    )
    db.session.add(item)
    db.session.commit()

    log_audit(identity, 'create', 'inventory_item', item.id, f"Created inventory item '{item.name}'")

    return jsonify({
        'message': 'Inventory item created successfully',
        'item': item.to_dict()
    }), 201


@inventory_bp.route('/<item_id>', methods=['PUT'])
@jwt_required()
def update_inventory_item(item_id):
    """Update an inventory item."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role not in ['admin', 'staff']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    item = InventoryItem.query.get(item_id)
    if not item:
        return jsonify({'error': 'Inventory item not found'}), 404
    
    data = request.get_json()
    
    if 'name' in data:
        item.name = data['name']
    if 'category' in data:
        item.category = (data['category'] or '').strip() or None
    if 'sku' in data:
        # Only query for a duplicate when the SKU is actually changing.
        # Re-reading this same document via .filter_by() while it's still
        # unchanged in Firestore creates a second, stale, unedited copy of
        # this item in the session's pending-write set — committed after
        # the real edits, it silently overwrites every field just changed
        # (name, stock, price, etc.) back to their old values.
        if data['sku'] != item.sku:
            existing = InventoryItem.query.filter_by(sku=data['sku']).first()
            if existing and existing.id != item_id:
                return jsonify({'error': 'SKU already exists'}), 400
        item.sku = data['sku']
    if 'description' in data:
        item.description = data['description']
    if 'stock' in data:
        stock, err = _non_negative_number(data['stock'], 'Stock')
        if err:
            return jsonify({'error': err}), 400
        item.stock = stock
    if 'price' in data:
        price, err = _non_negative_number(data['price'], 'Price')
        if err:
            return jsonify({'error': err}), 400
        item.price = price
    if 'cost' in data:
        cost, err = _non_negative_number(data['cost'], 'Cost', required=False)
        if err:
            return jsonify({'error': err}), 400
        item.cost = cost
    if 'reorder_level' in data:
        reorder_level, err = _non_negative_number(data['reorder_level'], 'Reorder level')
        if err:
            return jsonify({'error': err}), 400
        item.reorder_level = reorder_level
    if 'supplier' in data:
        item.supplier = data['supplier']
    if 'location' in data:
        item.location = data['location']
    if 'is_active' in data:
        item.is_active = data['is_active']
    
    db.session.commit()

    log_audit(identity, 'update', 'inventory_item', item.id, f"Updated inventory item '{item.name}'")

    return jsonify({
        'message': 'Inventory item updated successfully',
        'item': item.to_dict()
    }), 200


@inventory_bp.route('/<item_id>', methods=['DELETE'])
@jwt_required()
def delete_inventory_item(item_id):
    """Delete (deactivate) an inventory item."""
    identity = get_jwt_identity()
    role = identity.get('role')

    if role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    item = InventoryItem.query.get(item_id)
    if not item:
        return jsonify({'error': 'Inventory item not found'}), 404

    # Soft delete - just mark as inactive
    item.is_active = False
    db.session.commit()

    log_audit(identity, 'delete', 'inventory_item', item.id, f"Deactivated inventory item '{item.name}'")

    return jsonify({'message': 'Inventory item deactivated'}), 200


# Stock management
@inventory_bp.route('/<item_id>/stock', methods=['POST'])
@jwt_required()
def adjust_stock(item_id):
    """Adjust stock quantity (add or deduct)."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role not in ['admin', 'staff', 'mechanic']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    item = InventoryItem.query.get(item_id)
    if not item:
        return jsonify({'error': 'Inventory item not found'}), 404
    
    data = request.get_json()
    quantity = data.get('quantity')
    operation = data.get('operation', 'add')  # add or deduct
    
    if not quantity or quantity <= 0:
        return jsonify({'error': 'Valid quantity is required'}), 400
    
    if operation == 'add':
        # Use InventoryMovement.record_receipt for proper forecasting integration
        try:
            movement = InventoryMovement.record_receipt(
                inventory_item_id=item.id,
                quantity=quantity,
                unit_price=item.cost,
                purchase_order_num=None,
                reason=f'Manual stock addition via inventory management',
                user_id=identity.get('id')
            )
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
    elif operation == 'deduct':
        # Use InventoryMovement.record_usage for proper forecasting integration
        try:
            movement = InventoryMovement.record_usage(
                inventory_item_id=item.id,
                quantity=quantity,
                reference_type='manual_adjustment',
                reference_id=None,
                reason=f'Manual stock deduction via inventory management',
                user_id=identity.get('id')
            )
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
    else:
        return jsonify({'error': 'Invalid operation'}), 400
    
    db.session.commit()
    
    return jsonify({
        'message': f'Stock {operation}ed successfully',
        'item': item.to_dict()
    }), 200


@inventory_bp.route('/categories', methods=['GET'])
@jwt_required()
def get_categories():
    """Get all unique inventory categories."""
    items = InventoryItem.query.filter_by(is_active=True).all()
    # Case-insensitive sort so an inconsistently-capitalized category (free
    # text — see create/update) doesn't jump to the front of the dropdown
    # ahead of everything else purely because 'R' sorts before 'a'.
    categories = sorted({item.category for item in items if item.category}, key=str.lower)

    return jsonify(categories), 200


@inventory_bp.route('/low-stock', methods=['GET'])
@jwt_required()
def get_low_stock_items():
    """Get items that need reordering."""
    # Firestore can't compare two fields of the same document in a query
    # (stock vs reorder_level) — same avoidance pattern as get_inventory()'s
    # low_stock_only above; this endpoint just never got the same fix.
    items = InventoryItem.query.filter_by(is_active=True).all()
    items = [i for i in items if (i.stock or 0) <= (i.reorder_level or 0)]

    return jsonify([item.to_dict() for item in items]), 200


@inventory_bp.route('/critical-count', methods=['GET'])
@jwt_required()
def get_critical_stock_count():
    """Lightweight count of items at/below the CRITICAL threshold, for the
    sidebar's blinking indicator (checked on every page load across the
    whole app). Deliberately does NOT call
    InventoryAnalytics.calculate_reorder_point() like forecast.html's
    alert levels do -- that aggregates per-item movement history and is far
    too expensive to run for every item on every page load. Uses that same
    function's fallback ratio instead (critical = stock <= 25% of
    reorder_level, the exact threshold calculate_reorder_point falls back to
    when an item has no demand history) so the two "critical" definitions
    stay the same rule, just one cheap and one thorough.
    """
    items = InventoryItem.query.filter_by(is_active=True).all()
    critical = [i for i in items if (i.stock or 0) <= (i.reorder_level or 0) * 0.25]
    return jsonify({'critical_count': len(critical)}), 200
