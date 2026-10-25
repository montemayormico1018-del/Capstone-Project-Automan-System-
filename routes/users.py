from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models import db
from models.user import User
from firebase_config import create_firebase_user, delete_firebase_user, get_auth
from utils.audit import log_audit
import re

users_bp = Blueprint('users', __name__)


def admin_required():
    """Check if current user is admin."""
    identity = get_jwt_identity()
    return identity.get('role') == 'admin'


INTERNAL_ROLES = {'admin', 'staff', 'mechanic'}


def generate_internal_username(role):
    """Return the next never-reused role username (e.g. mechanic001)."""
    prefix = role.lower()
    highest = 0
    for user in User.query.all():
        match = re.fullmatch(rf'{re.escape(prefix)}(\d+)', (user.username or '').lower())
        if match:
            highest = max(highest, int(match.group(1)))
    return f'{prefix}{highest + 1:03d}'


@users_bp.route('', methods=['GET'])
@jwt_required()
def get_users():
    """Get all users (admin only)."""
    if not admin_required():
        return jsonify({'error': 'Admin access required'}), 403
    
    users = User.query.all()
    return jsonify([user.to_dict() for user in users]), 200


@users_bp.route('/<user_id>', methods=['GET'])
@jwt_required()
def get_user(user_id):
    """Get a specific user."""
    identity = get_jwt_identity()
    
    # Users can view their own profile or admin can view any
    if identity['id'] != user_id and identity['role'] != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify(user.to_dict()), 200


@users_bp.route('', methods=['POST'])
@jwt_required()
def create_user():
    """Create a new user (admin only)."""
    if not admin_required():
        return jsonify({'error': 'Admin access required'}), 403

    identity = get_jwt_identity()
    data = request.get_json()

    # Customer registration is handled exclusively by /api/auth/register.
    # Internal accounts receive a server-generated, immutable username.
    email = data.get('email')
    password = data.get('password')
    role = (data.get('role') or '').lower()
    first_name = (data.get('first_name') or '').strip()
    middle_initial = (data.get('middle_initial') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    contact_number = (data.get('contact_number') or '').strip()

    if role not in INTERNAL_ROLES:
        return jsonify({'error': 'Create only admin, staff, or mechanic accounts here'}), 400
    if not first_name or not last_name or not email or not password or not contact_number:
        return jsonify({'error': 'First name, last name, email, password, and contact number are required'}), 400
    # Same length/character rule as customer self-registration (routes/auth.py's
    # register()) -- this path had no such check, so an admin-created account's
    # name was unbounded and unrestricted in character set.
    name_pattern = r"^[A-Za-zÀ-ɏ' \-]+$"
    if len(first_name) > 20 or not re.match(name_pattern, first_name):
        return jsonify({'error': 'First name must be 20 characters or fewer and contain only letters'}), 400
    if len(last_name) > 20 or not re.match(name_pattern, last_name):
        return jsonify({'error': 'Last name must be 20 characters or fewer and contain only letters'}), 400
    if middle_initial and not re.match(r"^[A-Za-zÀ-ɏ]\.?$", middle_initial):
        return jsonify({'error': 'Middle initial must be a single letter (e.g. "C" or "C.")'}), 400
    if not re.match(r'^(09|\+63)\d{9}$', contact_number):
        return jsonify({'error': 'Invalid phone number format. Use 09XXXXXXXXX or +63XXXXXXXXX'}), 400

    username = generate_internal_username(role)

    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already exists'}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already exists'}), 400

    display_name = f'{first_name} {last_name}' if not middle_initial else f'{first_name} {middle_initial.rstrip(".")}. {last_name}'

    # Create the Firebase Auth account first (the UID becomes the Firestore doc id),
    # mirroring routes/auth.py's register()/reset_demo_accounts() flow.
    try:
        fb_user = create_firebase_user(email=email, password=password, display_name=display_name)
    except Exception as e:
        return jsonify({'error': f'Could not create Firebase account: {e}'}), 400

    user = User(
        id=fb_user.uid,
        username=username,
        first_name=first_name,
        middle_initial=middle_initial,
        last_name=last_name,
        email=email,
        role=role,
        contact_number=contact_number,
        availability='not_available' if role == 'mechanic' else None,
        is_active=True,
        is_verified=True
    )

    db.session.add(user)
    db.session.commit()

    log_audit(identity, 'create', 'user', user.id, f"Created {role} account '{user.username}'")

    return jsonify({
        'message': 'User created successfully',
        'user': user.to_dict()
    }), 201


@users_bp.route('/<user_id>', methods=['PUT'])
@jwt_required()
def update_user(user_id):
    """Update a user."""
    identity = get_jwt_identity()
    
    # Only admin or the user themselves can update
    if identity['id'] != user_id and identity['role'] != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    data = request.get_json()

    name_pattern = r"^[A-Za-zÀ-ɏ' \-]+$"
    if 'first_name' in data:
        first_name = (data['first_name'] or '').strip()
        if not first_name or len(first_name) > 20 or not re.match(name_pattern, first_name):
            return jsonify({'error': 'First name must be 20 characters or fewer and contain only letters'}), 400
        user.first_name = first_name
    if 'last_name' in data:
        last_name = (data['last_name'] or '').strip()
        if not last_name or len(last_name) > 20 or not re.match(name_pattern, last_name):
            return jsonify({'error': 'Last name must be 20 characters or fewer and contain only letters'}), 400
        user.last_name = last_name
    if 'middle_initial' in data:
        middle_initial = (data['middle_initial'] or '').strip()
        if middle_initial and not re.match(r"^[A-Za-zÀ-ɏ]\.?$", middle_initial):
            return jsonify({'error': 'Middle initial must be a single letter (e.g. "C" or "C.")'}), 400
        user.middle_initial = middle_initial
    if 'first_name' in data or 'last_name' in data or 'middle_initial' in data:
        user._sync_full_name()
    if 'contact_number' in data:
        contact_number = (data['contact_number'] or '').strip()
        if not contact_number or len(contact_number) > 13 or not re.match(r'^(09|\+63)\d{9}$', contact_number):
            return jsonify({'error': 'A valid contact number is required'}), 400
        user.contact_number = contact_number
    if 'email' in data and data['email'] != user.email:
        # Email is the account's login/auth identity. Letting a customer
        # change it freely from their own profile would silently desync it
        # from the Firebase Auth record (which is never updated here),
        # breaking their next login — so only an admin may change it, and
        # only through the dedicated user-management flow, never self-service.
        if identity['role'] != 'admin':
            return jsonify({'error': 'Email cannot be changed from your profile. Contact an administrator.'}), 403
        existing = User.query.filter_by(email=data['email']).first()
        if existing and existing.id != user_id:
            return jsonify({'error': 'Email already exists'}), 400
        try:
            get_auth().update_user(user_id, email=data['email'])
        except Exception as e:
            return jsonify({'error': f'Could not update Firebase account email: {e}'}), 400
        user.email = data['email']
    
    # Roles are historical account identities, never editable.  Promotions
    # must deactivate the old account and create a new internal account.
    if identity['role'] == 'admin':
        if 'role' in data and data['role'] != user.role:
            return jsonify({'error': 'Roles cannot be changed. Deactivate this account and create a new role account.'}), 400
        if 'is_active' in data:
            user.is_active = data['is_active']
    
    db.session.commit()

    changed_fields = [f for f in ('first_name', 'middle_initial', 'last_name', 'contact_number', 'email', 'is_active') if f in data]
    log_audit(identity, 'update', 'user', user.id, f"Updated account '{user.username}' (fields: {', '.join(changed_fields) or 'none'})")

    return jsonify({
        'message': 'User updated successfully',
        'user': user.to_dict()
    }), 200


@users_bp.route('/<user_id>', methods=['DELETE'])
@jwt_required()
def delete_user(user_id):
    """Delete a user (admin only)."""
    if not admin_required():
        return jsonify({'error': 'Admin access required'}), 403
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    # Prevent deleting yourself
    identity = get_jwt_identity()
    if identity['id'] == user_id:
        return jsonify({'error': 'Cannot delete your own account'}), 400
    
    # Delete the Firebase Auth account too, not just the Firestore profile,
    # otherwise the email stays taken and can never be re-registered.
    delete_firebase_user(user.id)

    deleted_username = user.username
    deleted_role = user.role
    db.session.delete(user)
    db.session.commit()

    log_audit(identity, 'delete', 'user', user_id, f"Deleted {deleted_role} account '{deleted_username}'")

    return jsonify({'message': 'User deleted successfully'}), 200


@users_bp.route('/mechanics', methods=['GET'])
@jwt_required()
def get_mechanics():
    """Get all mechanic users."""
    mechanics = User.query.filter_by(role='mechanic', is_active=True).all()
    return jsonify([m.to_dict() for m in mechanics]), 200


@users_bp.route('/mechanics/<mechanic_id>/availability', methods=['PUT'])
@jwt_required()
def update_mechanic_availability(mechanic_id):
    """Mechanics update themselves; admin/staff may update any mechanic."""
    identity = get_jwt_identity()
    mechanic = User.query.get(mechanic_id)
    if not mechanic or mechanic.role != 'mechanic':
        return jsonify({'error': 'Mechanic not found'}), 404
    if identity.get('role') not in ('admin', 'staff') and identity.get('id') != mechanic_id:
        return jsonify({'error': 'Unauthorized'}), 403
    availability = (request.get_json(silent=True) or {}).get('availability')
    if availability not in ('available', 'not_available'):
        return jsonify({'error': 'Availability must be available or not_available'}), 400
    mechanic.availability = availability
    db.session.commit()
    return jsonify({'message': 'Availability updated', 'mechanic': mechanic.to_dict()}), 200
