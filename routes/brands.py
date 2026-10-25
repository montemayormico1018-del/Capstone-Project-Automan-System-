from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models import db
from models.enhanced_models import CarBrand
from datetime import datetime

brands_bp = Blueprint('brands', __name__)

@brands_bp.route('', methods=['GET'])
@jwt_required()
def get_brands():
    """Get all active car brands"""
    try:
        # Sort in Python: filter + order_by together needs a Firestore
        # composite index that doesn't exist (500 in production).
        brands = sorted(
            CarBrand.query.filter_by(is_active=True).all(),
            key=lambda b: (b.name or '').lower()
        )
        return jsonify({
            'brands': [{
                'id': b.id,
                'name': b.name,
                'country': b.country,
                'logo_url': b.logo_url,
                'description': b.description
            } for b in brands]
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@brands_bp.route('', methods=['POST'])
@jwt_required()
def create_brand():
    """Create a new car brand. Admin/staff only — this is the make catalog
    customers pick from, not something they can write to themselves."""
    identity = get_jwt_identity()
    if identity.get('role') not in ('admin', 'staff'):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403
    try:
        data = request.get_json()
        
        if not data or not data.get('name'):
            return jsonify({'error': 'Brand name is required'}), 400
        
        # Check if brand already exists
        existing = CarBrand.query.filter_by(name=data['name']).first()
        if existing:
            if existing.is_active:
                return jsonify({'error': 'Brand already exists'}), 409
            else:
                # Reactivate inactive brand
                existing.is_active = True
                existing.updated_at = datetime.utcnow()
                db.session.commit()
                return jsonify({
                    'id': existing.id,
                    'name': existing.name,
                    'message': 'Brand reactivated'
                }), 201
        
        # Create new brand
        brand = CarBrand(
            name=data['name'],
            country=data.get('country', 'Philippines'),
            logo_url=data.get('logo_url', ''),
            description=data.get('description', ''),
            is_active=True
        )
        
        db.session.add(brand)
        db.session.commit()
        
        return jsonify({
            'id': brand.id,
            'name': brand.name,
            'country': brand.country,
            'message': 'Brand created successfully'
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@brands_bp.route('/<brand_id>', methods=['GET'])
@jwt_required()
def get_brand(brand_id):
    """Get a specific car brand"""
    try:
        brand = CarBrand.query.get(brand_id)
        
        if not brand:
            return jsonify({'error': 'Brand not found'}), 404
        
        return jsonify({
            'id': brand.id,
            'name': brand.name,
            'country': brand.country,
            'logo_url': brand.logo_url,
            'description': brand.description,
            'is_active': brand.is_active
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@brands_bp.route('/<brand_id>', methods=['PUT'])
@jwt_required()
def update_brand(brand_id):
    """Update a car brand. Admin/staff only."""
    identity = get_jwt_identity()
    if identity.get('role') not in ('admin', 'staff'):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403
    try:
        brand = CarBrand.query.get(brand_id)
        
        if not brand:
            return jsonify({'error': 'Brand not found'}), 404
        
        data = request.get_json()
        
        if 'name' in data:
            brand.name = data['name']
        if 'country' in data:
            brand.country = data['country']
        if 'logo_url' in data:
            brand.logo_url = data['logo_url']
        if 'description' in data:
            brand.description = data['description']
        if 'is_active' in data:
            brand.is_active = data['is_active']
        
        brand.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'id': brand.id,
            'name': brand.name,
            'message': 'Brand updated successfully'
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@brands_bp.route('/<brand_id>', methods=['DELETE'])
@jwt_required()
def delete_brand(brand_id):
    """Delete (soft delete) a car brand. Admin/staff only."""
    identity = get_jwt_identity()
    if identity.get('role') not in ('admin', 'staff'):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403
    try:
        brand = CarBrand.query.get(brand_id)
        
        if not brand:
            return jsonify({'error': 'Brand not found'}), 404
        
        brand.is_active = False
        brand.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({'message': 'Brand deleted successfully'}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
