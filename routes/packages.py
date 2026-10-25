from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime
from models import db
from models.service_package import ServicePackage
from models.package_item import PackageItem
from models.vehicle import Vehicle
from utils.vehicle_applicability import (
    normalize_vehicle_powertrain, is_service_applicable_by_flag,
)

packages_bp = Blueprint('packages', __name__)


def _admin_staff_required(identity):
    return identity.get('role') in ('admin', 'staff')


@packages_bp.route('', methods=['GET'])
@jwt_required()
def get_packages():
    """List active packages, optionally filtered/scoped by vehicle_id."""
    try:
        vehicle_id = request.args.get('vehicle_id')
        packages = ServicePackage.query.filter_by(is_active=True).all()
        ServicePackage.prefetch(packages)

        if not vehicle_id:
            return jsonify([p.to_dict() for p in packages]), 200

        vehicle = Vehicle.query.get(vehicle_id)
        if not vehicle:
            return jsonify({'error': 'Vehicle not found'}), 404

        powertrain = normalize_vehicle_powertrain(vehicle)
        compatible = [p for p in packages if is_service_applicable_by_flag(p, powertrain)]
        return jsonify([p.to_dict(vehicle=vehicle) for p in compatible]), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@packages_bp.route('/<package_id>', methods=['GET'])
@jwt_required()
def get_package(package_id):
    package = ServicePackage.query.get(package_id)
    if not package:
        return jsonify({'error': 'Package not found'}), 404

    vehicle_id = request.args.get('vehicle_id')
    vehicle = Vehicle.query.get(vehicle_id) if vehicle_id else None
    return jsonify(package.to_dict(vehicle=vehicle)), 200


@packages_bp.route('', methods=['POST'])
@jwt_required()
def create_package():
    identity = get_jwt_identity()
    if not _admin_staff_required(identity):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403

    data = request.get_json()
    if not data.get('name'):
        return jsonify({'error': 'Package name is required'}), 400

    package = ServicePackage(
        name=data['name'],
        description=data.get('description'),
        base_price=data.get('base_price', 0.0),
        price_small=data.get('price_small'),
        price_large=data.get('price_large'),
        estimated_duration_minutes=data.get('estimated_duration_minutes'),
        applicable_gasoline=data.get('applicable_gasoline', True),
        applicable_diesel=data.get('applicable_diesel', True),
        applicable_hev=data.get('applicable_hev', True),
        applicable_phev=data.get('applicable_phev', True),
        applicable_bev=data.get('applicable_bev', True),
    )
    db.session.add(package)
    db.session.commit()

    for idx, item_data in enumerate(data.get('items', [])):
        if not item_data.get('item_name'):
            continue
        item = PackageItem(
            package_id=package.id,
            item_name=item_data['item_name'],
            item_description=item_data.get('item_description'),
            sequence=item_data.get('sequence', idx),
            applicable_gasoline=item_data.get('applicable_gasoline', True),
            applicable_diesel=item_data.get('applicable_diesel', True),
            applicable_hev=item_data.get('applicable_hev', True),
            applicable_phev=item_data.get('applicable_phev', True),
            applicable_bev=item_data.get('applicable_bev', True),
        )
        db.session.add(item)
    db.session.commit()

    return jsonify({
        'message': 'Package created successfully',
        'package': package.to_dict()
    }), 201


@packages_bp.route('/<package_id>', methods=['PUT'])
@jwt_required()
def update_package(package_id):
    identity = get_jwt_identity()
    if not _admin_staff_required(identity):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403

    package = ServicePackage.query.get(package_id)
    if not package:
        return jsonify({'error': 'Package not found'}), 404

    data = request.get_json()
    for field in ('name', 'description', 'base_price', 'price_small', 'price_large',
                  'estimated_duration_minutes', 'is_active', 'applicable_gasoline',
                  'applicable_diesel', 'applicable_hev', 'applicable_phev', 'applicable_bev'):
        if field in data:
            setattr(package, field, data[field])
    package.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        'message': 'Package updated successfully',
        'package': package.to_dict()
    }), 200


@packages_bp.route('/<package_id>', methods=['DELETE'])
@jwt_required()
def delete_package(package_id):
    """Soft delete (is_active=False), matching CarBrand's precedent."""
    identity = get_jwt_identity()
    if not _admin_staff_required(identity):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403

    package = ServicePackage.query.get(package_id)
    if not package:
        return jsonify({'error': 'Package not found'}), 404

    package.is_active = False
    package.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({'message': 'Package deleted successfully'}), 200


# --- Package Items sub-resource -------------------------------------------

@packages_bp.route('/<package_id>/items', methods=['GET'])
@jwt_required()
def get_package_items(package_id):
    items = PackageItem.query.filter_by(package_id=package_id).all()
    items.sort(key=lambda i: (i.sequence if i.sequence is not None else 0))
    return jsonify([i.to_dict() for i in items]), 200


@packages_bp.route('/<package_id>/items', methods=['POST'])
@jwt_required()
def add_package_item(package_id):
    identity = get_jwt_identity()
    if not _admin_staff_required(identity):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403

    package = ServicePackage.query.get(package_id)
    if not package:
        return jsonify({'error': 'Package not found'}), 404

    data = request.get_json()
    if not data.get('item_name'):
        return jsonify({'error': 'item_name is required'}), 400

    item = PackageItem(
        package_id=package_id,
        item_name=data['item_name'],
        item_description=data.get('item_description'),
        sequence=data.get('sequence', len(package.items)),
        applicable_gasoline=data.get('applicable_gasoline', True),
        applicable_diesel=data.get('applicable_diesel', True),
        applicable_hev=data.get('applicable_hev', True),
        applicable_phev=data.get('applicable_phev', True),
        applicable_bev=data.get('applicable_bev', True),
    )
    db.session.add(item)
    db.session.commit()

    return jsonify({'message': 'Item added', 'item': item.to_dict()}), 201


@packages_bp.route('/items/<item_id>', methods=['PUT'])
@jwt_required()
def update_package_item(item_id):
    identity = get_jwt_identity()
    if not _admin_staff_required(identity):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403

    item = PackageItem.query.get(item_id)
    if not item:
        return jsonify({'error': 'Item not found'}), 404

    data = request.get_json()
    for field in ('item_name', 'item_description', 'sequence',
                  'applicable_gasoline', 'applicable_diesel',
                  'applicable_hev', 'applicable_phev', 'applicable_bev'):
        if field in data:
            setattr(item, field, data[field])
    db.session.commit()

    return jsonify({'message': 'Item updated', 'item': item.to_dict()}), 200


@packages_bp.route('/items/<item_id>', methods=['DELETE'])
@jwt_required()
def delete_package_item(item_id):
    identity = get_jwt_identity()
    if not _admin_staff_required(identity):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403

    item = PackageItem.query.get(item_id)
    if not item:
        return jsonify({'error': 'Item not found'}), 404

    db.session.delete(item)
    db.session.commit()

    return jsonify({'message': 'Item deleted'}), 200
