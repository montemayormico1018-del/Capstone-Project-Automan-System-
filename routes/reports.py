from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta
from models.appointment import Appointment
from models.job_order import JobOrder
from models.sale import Sale
from models.inventory import InventoryItem

reports_bp = Blueprint('reports', __name__)


@reports_bp.route('/appointments', methods=['GET'])
@jwt_required()
def appointment_report():
    """Generate appointment report."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role == 'customer':
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Get date range
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    status = request.args.get('status')
    
    query = Appointment.query
    
    if start_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d').date()
            query = query.filter(Appointment.preferred_date >= start)
        except ValueError:
            pass
    
    if end_date:
        try:
            end = datetime.strptime(end_date, '%Y-%m-%d').date()
            query = query.filter(Appointment.preferred_date <= end)
        except ValueError:
            pass
    
    if status:
        query = query.filter_by(status=status)
    
    appointments = query.order_by(Appointment.preferred_date.desc()).all()
    Appointment.prefetch(appointments)

    # Summary stats — counted in Python from the already-fetched list rather
    # than issuing separate .filter_by(status=...) queries. Chaining a status
    # filter onto a query that already has the start/end date range applied
    # needs a Firestore composite index (date range + status) that doesn't
    # exist here, so as soon as a date filter was used this 500'd.
    total = len(appointments)
    pending = sum(1 for a in appointments if a.status == 'Pending')
    approved = sum(1 for a in appointments if a.status == 'Approved')
    rejected = sum(1 for a in appointments if a.status == 'Rejected')
    cancelled = sum(1 for a in appointments if a.status == 'Cancelled')

    return jsonify({
        'appointments': [apt.to_dict() for apt in appointments],
        'summary': {
            'total': total,
            'pending': pending,
            'approved': approved,
            'rejected': rejected,
            'cancelled': cancelled
        }
    }), 200


@reports_bp.route('/job-orders', methods=['GET'])
@jwt_required()
def job_order_report():
    """Generate job order report."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role == 'customer':
        return jsonify({'error': 'Unauthorized'}), 403
    
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    status = request.args.get('status')
    
    query = JobOrder.query
    
    if start_date:
        try:
            # JobOrder.created_at is stored as a native Firestore Timestamp
            # (datetime.utcnow(), not the ISO-string dates Appointment uses)
            # — filtering with a bare .date() gets stringified by the query
            # layer's Firestore encoding and can never match a Timestamp
            # field, silently returning zero rows regardless of the actual
            # dates present. A plain datetime (midnight of that day) matches
            # the stored type correctly.
            start = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(JobOrder.created_at >= start)
        except ValueError:
            pass

    if end_date:
        try:
            # Exclusive upper bound at the start of the next day, so
            # anything created any time during end_date itself is included
            # (an inclusive <= end_date would incorrectly cut off everything
            # after midnight on that day, since the field carries a real
            # time-of-day, not just a date).
            end = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(JobOrder.created_at < end)
        except ValueError:
            pass

    if status:
        query = query.filter_by(status=status)

    job_orders = query.order_by(JobOrder.created_at.desc()).all()
    JobOrder.prefetch(job_orders)

    # Summary stats — counted in Python (see appointment_report for why:
    # a status filter chained onto an already date-filtered query needs a
    # composite index this project doesn't have).
    total = len(job_orders)
    pending = sum(1 for jo in job_orders if jo.status == 'Pending')
    in_progress = sum(1 for jo in job_orders if jo.status == 'In Progress')
    completed = sum(1 for jo in job_orders if jo.status == 'Completed')

    total_revenue = sum(jo.total_cost for jo in job_orders if jo.status == 'Completed')
    
    return jsonify({
        'job_orders': [jo.to_dict() for jo in job_orders],
        'summary': {
            'total': total,
            'pending': pending,
            'in_progress': in_progress,
            'completed': completed,
            'total_revenue': total_revenue
        }
    }), 200


@reports_bp.route('/inventory', methods=['GET'])
@jwt_required()
def inventory_report():
    """Generate inventory report."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role == 'customer':
        return jsonify({'error': 'Unauthorized'}), 403
    
    category = request.args.get('category')
    low_stock = request.args.get('low_stock')
    
    query = InventoryItem.query.filter_by(is_active=True)
    
    if category:
        query = query.filter_by(category=category)
    
    items = query.all()
    
    total_items = len(items)
    total_value = sum(item.stock * item.price for item in items)
    low_stock_items = [item for item in items if item.needs_reorder]
    
    # Group by category
    categories = {}
    for item in items:
        cat = item.category or 'Uncategorized'
        if cat not in categories:
            categories[cat] = {'count': 0, 'value': 0}
        categories[cat]['count'] += 1
        categories[cat]['value'] += item.stock * item.price
    
    return jsonify({
        'items': [item.to_dict() for item in items],
        'summary': {
            'total_items': total_items,
            'total_value': total_value,
            'low_stock_count': len(low_stock_items),
            'categories': categories
        }
    }), 200


@reports_bp.route('/sales', methods=['GET'])
@jwt_required()
def sales_report():
    """Generate sales report."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role == 'customer':
        return jsonify({'error': 'Unauthorized'}), 403
    
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    payment_status = request.args.get('payment_status')
    
    query = Sale.query
    
    if start_date:
        try:
            # Sale.created_at is a native Firestore Timestamp — see the same
            # fix/explanation in job_order_report(). A bare .date() here
            # always returned zero rows.
            start = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(Sale.created_at >= start)
        except ValueError:
            pass

    if end_date:
        try:
            end = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(Sale.created_at < end)
        except ValueError:
            pass
    
    if payment_status:
        query = query.filter_by(payment_status=payment_status)
    
    sales = query.order_by(Sale.created_at.desc()).all()
    Sale.prefetch(sales)

    # Summary stats — counted in Python (see appointment_report for why:
    # a status filter chained onto an already date-filtered query needs a
    # composite index this project doesn't have — this was the exact query
    # that 500'd as soon as a date range was applied).
    total_sales = len(sales)
    total_revenue = sum(sale.total for sale in sales)
    total_paid = sum(sale.amount_paid for sale in sales)
    total_pending = total_revenue - total_paid
    total_parts_cost = sum(sale.parts_cost for sale in sales)
    total_profit = sum(sale.profit for sale in sales)

    paid_count = sum(1 for s in sales if s.payment_status == 'Paid')
    pending_count = sum(1 for s in sales if s.payment_status == 'Pending')
    partial_count = sum(1 for s in sales if s.payment_status == 'Partial')

    return jsonify({
        'sales': [sale.to_dict() for sale in sales],
        'summary': {
            'total_sales': total_sales,
            'total_revenue': total_revenue,
            'total_paid': total_paid,
            'total_pending': total_pending,
            'total_parts_cost': round(total_parts_cost, 2),
            'total_profit': round(total_profit, 2),
            'paid_count': paid_count,
            'pending_count': pending_count,
            'partial_count': partial_count
        }
    }), 200


@reports_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def dashboard_report():
    """Get dashboard statistics."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role == 'customer':
        return jsonify({'error': 'Unauthorized'}), 403
    
    today = datetime.now().date()
    start_of_month = today.replace(day=1)
    
    # Appointment stats
    total_appointments = Appointment.query.count()
    pending_appointments = Appointment.query.filter_by(status='Pending').count()
    today_appointments = Appointment.query.filter_by(preferred_date=today).count()
    
    # Job order stats
    total_job_orders = JobOrder.query.count()
    pending_job_orders = JobOrder.query.filter_by(status='Pending').count()
    in_progress_job_orders = JobOrder.query.filter_by(status='In Progress').count()
    completed_job_orders = JobOrder.query.filter_by(status='Completed').count()
    
    # Sales stats
    monthly_sales = Sale.query.filter(Sale.created_at >= start_of_month).all()
    total_monthly_revenue = sum(s.total for s in monthly_sales)
    paid_monthly = sum(s.amount_paid for s in monthly_sales)
    
    # Inventory stats. Firestore can't compare two document fields in a
    # query (stock <= reorder_level), so count low stock in Python.
    active_items = InventoryItem.query.filter_by(is_active=True).all()
    total_inventory_items = len(active_items)
    low_stock_items = sum(
        1 for item in active_items
        if (item.stock or 0) <= (item.reorder_level or 0)
    )
    
    return jsonify({
        'appointments': {
            'total': total_appointments,
            'pending': pending_appointments,
            'today': today_appointments
        },
        'job_orders': {
            'total': total_job_orders,
            'pending': pending_job_orders,
            'in_progress': in_progress_job_orders,
            'completed': completed_job_orders
        },
        'sales': {
            'monthly_revenue': total_monthly_revenue,
            'monthly_paid': paid_monthly,
            'monthly_pending': total_monthly_revenue - paid_monthly
        },
        'inventory': {
            'total_items': total_inventory_items,
            'low_stock': low_stock_items
        }
    }), 200
