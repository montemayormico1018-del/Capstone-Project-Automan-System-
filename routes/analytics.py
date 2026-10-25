"""
Inventory Forecasting Routes
API endpoints for inventory restocking forecasts, used by the Forecast page.
"""

from flask import Blueprint, jsonify, request, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from models.inventory import InventoryItem
from models.enhanced_models import InventoryMovement
from utils.database_analytics import InventoryAnalytics

analytics_bp = Blueprint('analytics', __name__, url_prefix='/api/analytics')


def _admin_staff_required():
    # Mechanics get read access too: the Forecast page is part of their
    # workflow and every endpoint in this blueprint is GET-only.
    identity = get_jwt_identity()
    return identity.get('role') in ('admin', 'staff', 'mechanic')


@analytics_bp.route('/inventory/items-below-reorder', methods=['GET'])
@jwt_required()
def get_items_below_reorder():
    """Get inventory items below reorder level. Admin/staff only — stock
    levels and suppliers, same protection as routes/inventory.py."""
    if not _admin_staff_required():
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    try:
        items = InventoryAnalytics.get_items_below_reorder_level()

        return jsonify({
            'status': 'success',
            'count': len(items),
            'items': [
                {
                    'id': i[0],
                    'name': i[1],
                    'sku': i[2],
                    'current_stock': i[3],
                    'reorder_level': i[4],
                    'shortage_quantity': i[5],
                    'supplier': i[6]
                }
                for i in items
            ]
        }), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@analytics_bp.route('/inventory/<item_id>/monthly-usage', methods=['GET'])
@jwt_required()
def get_monthly_usage(item_id):
    """Get monthly usage data for an inventory item. Admin/staff only."""
    if not _admin_staff_required():
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    try:
        months = request.args.get('months', 6, type=int)

        item = InventoryItem.query.get(item_id)
        if not item:
            return jsonify({'status': 'error', 'message': 'Item not found'}), 404

        monthly_data = InventoryAnalytics.get_monthly_usage(item_id, months)

        return jsonify({
            'status': 'success',
            'item': item.to_dict(),
            'months': months,
            'data': [
                {
                    'month': m[0],
                    'quantity_used': m[1] if m[1] else 0,
                    'total_cost': float(m[2]) if m[2] else 0.0
                }
                for m in monthly_data
            ]
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@analytics_bp.route('/inventory/<item_id>/statistics', methods=['GET'])
@jwt_required()
def get_inventory_statistics(item_id):
    """Get statistics for an inventory item. Admin/staff only."""
    if not _admin_staff_required():
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    try:
        months = request.args.get('months', 6, type=int)

        item = InventoryItem.query.get(item_id)
        if not item:
            return jsonify({'status': 'error', 'message': 'Item not found'}), 404

        stats = InventoryAnalytics.calculate_average_monthly_demand(item_id, months)

        return jsonify({
            'status': 'success',
            'item': item.to_dict(),
            'statistics': stats
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@analytics_bp.route('/inventory/<item_id>/forecast', methods=['GET'])
@jwt_required()
def get_inventory_forecast(item_id):
    """Forecast next month's demand for an inventory item with alerts. Admin/staff only."""
    if not _admin_staff_required():
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    try:
        months = request.args.get('months', 6, type=int)

        item = InventoryItem.query.get(item_id)
        if not item:
            return jsonify({'status': 'error', 'message': 'Item not found'}), 404

        _cache = {}
        forecast = InventoryAnalytics.forecast_next_month_demand(item_id, months, _cache)
        reorder_point_data = InventoryAnalytics.calculate_reorder_point(item_id, months, _cache, item)
        trend = InventoryAnalytics.get_stock_trend_analysis(item_id, months, _cache)

        # Determine alert level
        alert_level = 'OK'
        if isinstance(reorder_point_data, dict):
            if item.stock <= reorder_point_data['critical_level']:
                alert_level = 'CRITICAL'
            elif item.stock <= reorder_point_data['warning_level']:
                alert_level = 'WARNING'
            elif item.stock <= reorder_point_data['reorder_point']:
                alert_level = 'REORDER'
        else:
            if item.stock <= reorder_point_data:
                alert_level = 'REORDER'

        return jsonify({
            'status': 'success',
            'item': item.to_dict(),
            'forecast': forecast,
            'suggested_reorder_point': reorder_point_data,
            'trend': trend,
            'alert_level': alert_level,
            'recommendation': f"Current stock: {item.stock} units. Alert: {alert_level}"
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@analytics_bp.route('/inventory/forecast-all', methods=['GET'])
@jwt_required()
def get_all_forecasts():
    """Get forecasts for all inventory items with alert escalation. Admin/staff only."""
    if not _admin_staff_required():
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    try:
        items = InventoryItem.query.filter_by(is_active=True, is_deleted=False).all()
        # One fetch of the whole movements collection instead of two
        # (.count() itself does a full scan — see firestore_db.py's Query.count
        # — so a separate .with_entities().all() right after it was a second,
        # identical full scan just to also get the distinct item ids).
        all_movements = InventoryMovement.query.all()
        movement_count = len(all_movements)
        item_history_count = len({m.inventory_item_id for m in all_movements})
        forecasts = []
        critical_items = []
        warning_items = []

        # Shared across every item below — see get_monthly_usage's docstring.
        # Without it, forecasting 21 items took 83 Firestore queries (~8.5s).
        # Pre-grouping all_movements (already fetched above for the counts)
        # by item and seeding the cache with it collapses the remaining
        # one-query-per-item down to zero — every item's usage now comes
        # from the single collection-wide fetch already sitting in memory.
        _cache = {}
        movements_by_item = {}
        for m in all_movements:
            movements_by_item.setdefault(str(m.inventory_item_id), []).append(m)
        for item in items:
            _cache[('movements', str(item.id))] = movements_by_item.get(str(item.id), [])

        for item in items:
            try:
                forecast = InventoryAnalytics.forecast_next_month_demand(item.id, _cache=_cache)
                reorder_point_data = InventoryAnalytics.calculate_reorder_point(item.id, _cache=_cache, item=item)
                trend = InventoryAnalytics.get_stock_trend_analysis(item.id, _cache=_cache)

                # Determine alert level
                alert_level = 'OK'
                if isinstance(reorder_point_data, dict):
                    if item.stock <= reorder_point_data['critical_level']:
                        alert_level = 'CRITICAL'
                    elif item.stock <= reorder_point_data['warning_level']:
                        alert_level = 'WARNING'
                    elif item.stock <= reorder_point_data['reorder_point']:
                        alert_level = 'REORDER'
                    reorder_point = reorder_point_data['reorder_point']
                else:
                    if item.stock <= reorder_point_data:
                        alert_level = 'REORDER'
                    reorder_point = reorder_point_data

            except Exception as inner_e:
                current_app.logger.exception('Forecasting failed for item %s: %s', item.id, inner_e)
                forecast = {'forecasted_demand': 0, 'confidence': 'VERY_LOW', 'notes': 'Error calculating'}
                reorder_point_data = item.reorder_level
                reorder_point = item.reorder_level
                trend = 'INSUFFICIENT_DATA'
                alert_level = 'OK'

            item_forecast = {
                'id': item.id,
                'name': item.name,
                'sku': item.sku,
                'category': item.category,
                'current_stock': item.stock,
                'reorder_level': item.reorder_level,
                'forecast': forecast,
                'suggested_reorder_point': reorder_point_data,
                'trend': trend,
                'alert_level': alert_level,
                'needs_attention': alert_level in ['CRITICAL', 'WARNING', 'REORDER']
            }
            forecasts.append(item_forecast)

            if alert_level == 'CRITICAL':
                critical_items.append(item_forecast)
            elif alert_level == 'WARNING':
                warning_items.append(item_forecast)

        return jsonify({
            'status': 'success',
            'count': len(forecasts),
            'items_needing_reorder': len([f for f in forecasts if f['needs_attention']]),
            'critical_items': len(critical_items),
            'warning_items': len(warning_items),
            'inventory_movement_count': movement_count,
            'items_with_history': item_history_count,
            'forecasts': forecasts
        }), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500
