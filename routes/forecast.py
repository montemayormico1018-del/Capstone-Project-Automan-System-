from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, date
from models import db
from models.analytics import ServiceDemandLog, InventoryUsageLog, ForecastLog
from models.service import Service
from models.inventory import InventoryItem
from utils.database_analytics import InventoryAnalytics

forecast_bp = Blueprint('forecast', __name__)


@forecast_bp.route('', methods=['GET'])
@jwt_required()
def get_forecast():
    """Get forecast data (placeholder - Coming Soon)."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role == 'customer':
        return jsonify({'error': 'Unauthorized'}), 403
    
    # This is a placeholder - returns Coming Soon message
    return jsonify({
        'message': 'Coming Soon',
        'description': 'Predictive analytics for restocking forecasts is under development.',
        'available': False
    }), 200


@forecast_bp.route('/service-demand', methods=['GET'])
@jwt_required()
def get_service_demand_logs():
    """Get service demand historical data."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role == 'customer':
        return jsonify({'error': 'Unauthorized'}), 403
    
    service_id = request.args.get('service_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    query = ServiceDemandLog.query
    
    if service_id:
        query = query.filter_by(service_id=service_id)
    
    if start_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d').date()
            query = query.filter(ServiceDemandLog.date >= start)
        except ValueError:
            pass
    
    if end_date:
        try:
            end = datetime.strptime(end_date, '%Y-%m-%d').date()
            query = query.filter(ServiceDemandLog.date <= end)
        except ValueError:
            pass
    
    logs = query.order_by(ServiceDemandLog.date.desc()).all()
    
    return jsonify({
        'logs': [log.to_dict() for log in logs],
        'message': 'Historical data only - forecasting coming soon'
    }), 200


@forecast_bp.route('/inventory-usage', methods=['GET'])
@jwt_required()
def get_inventory_usage_logs():
    """Get inventory usage historical data."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role == 'customer':
        return jsonify({'error': 'Unauthorized'}), 403
    
    item_id = request.args.get('item_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    query = InventoryUsageLog.query
    
    if item_id:
        query = query.filter_by(inventory_item_id=item_id)
    
    if start_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d').date()
            query = query.filter(InventoryUsageLog.date >= start)
        except ValueError:
            pass
    
    if end_date:
        try:
            end = datetime.strptime(end_date, '%Y-%m-%d').date()
            query = query.filter(InventoryUsageLog.date <= end)
        except ValueError:
            pass
    
    logs = query.order_by(InventoryUsageLog.date.desc()).all()
    
    return jsonify({
        'logs': [log.to_dict() for log in logs],
        'message': 'Historical data only - forecasting coming soon'
    }), 200


@forecast_bp.route('/logs', methods=['GET'])
@jwt_required()
def get_forecast_logs():
    """Get forecast logs."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role == 'customer':
        return jsonify({'error': 'Unauthorized'}), 403
    
    forecast_type = request.args.get('type')
    
    query = ForecastLog.query
    
    if forecast_type:
        query = query.filter_by(forecast_type=forecast_type)
    
    logs = query.order_by(ForecastLog.forecast_date.desc()).all()
    
    return jsonify({
        'logs': [log.to_dict() for log in logs],
        'message': 'Forecast logs - predictive analytics coming soon'
    }), 200


@forecast_bp.route('/status', methods=['GET'])
@jwt_required()
def get_forecast_status():
    """Get predictive analytics feature status — reports what's actually live."""
    identity = get_jwt_identity()
    role = identity.get('role')

    if role == 'customer':
        return jsonify({'error': 'Unauthorized'}), 403

    # Firestore has no server-side aggregate/GROUP BY, so — same pattern as
    # routes/reports.py dashboard_report — pull active items once and compute
    # forecast-readiness in Python rather than per-item queries.
    active_items = InventoryItem.query.filter_by(is_active=True).all()
    total_active_items = len(active_items)

    # "Enough history to forecast" = at least 3 months with real recorded
    # usage. get_all_items_needing_forecast() returns every active item
    # regardless of history, so it can't answer this on its own.
    forecastable_count = 0
    for item in active_items:
        stats = InventoryAnalytics.calculate_average_monthly_demand(item.id)
        if stats['months_active'] >= 3:
            forecastable_count += 1

    return jsonify({
        'status': 'active',
        'message': 'Restocking forecast is live for inventory items with sufficient usage history.',
        'features': {
            'inventory_restocking_predictions': True,
            'trend_analysis': True,
            'service_demand_forecasting': False
        },
        'service_demand_forecasting_status': 'planned',
        'service_demand_forecasting_note': (
            'Service demand is logged (see ServiceDemandLog) but no forecast '
            'is generated from it yet.'
        ),
        'forecast_method': (
            'Simple Moving Average (3-6 month window) with trend adjustment '
            'and confidence rating'
        ),
        'metadata': {
            'total_active_inventory_items': total_active_items,
            'items_with_forecastable_history': forecastable_count,
            'items_awaiting_history': total_active_items - forecastable_count
        }
    }), 200
