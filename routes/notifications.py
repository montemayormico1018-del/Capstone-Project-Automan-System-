from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models.user import User

notifications_bp = Blueprint('notifications', __name__)


@notifications_bp.route('/register-device', methods=['POST'])
@jwt_required()
def register_device():
    """Register this device's FCM token against the logged-in account, so
    the backend knows where to push notifications for it (stock-reorder
    alerts for admin/staff, "your vehicle service started" for customers).

    Called only from inside the Capacitor app (window.Capacitor guard on
    the frontend) -- a normal browser session never has an FCM token to
    send here, so this is a no-op cost for anyone using the site normally.
    """
    identity = get_jwt_identity()
    user = User.query.get(identity.get('id'))
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    if not token:
        return jsonify({'error': 'token is required'}), 400

    user.add_fcm_token(token)
    user.save()
    return jsonify({'message': 'Device registered'}), 200


@notifications_bp.route('/unregister-device', methods=['POST'])
@jwt_required()
def unregister_device():
    """Drop a device token, called on logout so a signed-out device stops
    receiving pushes for the account it's no longer logged into."""
    identity = get_jwt_identity()
    user = User.query.get(identity.get('id'))
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    if not token:
        return jsonify({'error': 'token is required'}), 400

    user.remove_fcm_token(token)
    user.save()
    return jsonify({'message': 'Device unregistered'}), 200
