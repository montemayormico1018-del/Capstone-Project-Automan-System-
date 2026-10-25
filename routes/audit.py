from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models.audit_log import AuditLog

audit_bp = Blueprint('audit', __name__)


@audit_bp.route('', methods=['GET'])
@jwt_required()
def list_audit_logs():
    """Admin-only view of the accountability trail written by utils/audit.py's
    log_audit(): inventory items, sales, job orders, appointments (status/
    price/reschedule), vehicles, user accounts, and login/login_failed.

    Optional filters: entity_type ('inventory_item' | 'sale' | 'job_order' |
    'appointment' | 'vehicle' | 'user'), action ('create' | 'update' |
    'delete' | 'login' | 'login_failed'). Sorted newest first in Python,
    not a Firestore order_by, to avoid needing a composite index (same
    pattern used across the rest of this data layer).
    """
    identity = get_jwt_identity()
    if identity.get('role') != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    entity_type = request.args.get('entity_type')
    action = request.args.get('action')
    try:
        limit = min(int(request.args.get('limit', 200)), 500)
    except ValueError:
        limit = 200

    entries = AuditLog.query.all()
    if entity_type:
        entries = [e for e in entries if e.entity_type == entity_type]
    if action:
        entries = [e for e in entries if e.action == action]

    entries.sort(key=lambda e: e.created_at or '', reverse=True)
    entries = entries[:limit]

    return jsonify({
        'logs': [e.to_dict() for e in entries],
        'count': len(entries)
    }), 200
