# Implementation Guide - Database Migration & Setup

## Step 1: Review Current Structure vs. New Design

### Key Changes:
1. **New Tables**:
   - `car_brands` - Vehicle brand master data
   - `vehicle_types` - Powertrain types (BEV, HEV, etc.) with service flags
   - `service_vehicle_type_compatibility` - Service applicability matrix
   - `inventory_movements` - Core tracking table for all stock movements
   - `job_order_services` - Explicit service tracking in job orders
   - `appointment_services` - Services requested in appointments
   - `audit_logs` (optional) - Compliance tracking

2. **Enhanced Existing Tables**:
   - Add soft delete fields to: vehicles, appointments, job_orders, job_order_items, sales, sale_items
   - Rename and restructure fields for consistency
   - Add status tracking fields

3. **Removed/Deprecated**:
   - Remove direct `stock` modifications - use inventory_movements instead
   - Remove `appointment_services` association table if exists (replace with explicit model)
   - Remove `job_order_services` table if exists (replace with explicit model)

---

## Step 2: Python/SQLAlchemy Migration Path

### Option A: Using Flask-Migrate (Recommended)

```bash
# Install if not already installed
pip install Flask-Migrate

# Initialize migrations
flask db init

# Generate migration
flask db migrate -m "Add soft deletes and inventory movements"

# Apply migration
flask db upgrade
```

### Option B: Manual Database Creation

1. Backup your current database
2. Create new tables using the SQL from `DATABASE_DESIGN.md`
3. Migrate data from old tables to new tables

---

## Step 3: Update Models - Integration Steps

### 3.1 Backup Current Models
```bash
# Keep originals for reference
copy models\service.py models\service.backup.py
copy models\job_order.py models\job_order.backup.py
# ... etc for all model files
```

### 3.2 Update Model Imports

**File: `models/__init__.py`**

Add to imports:
```python
from models.enhanced_models import (
    CarBrand,
    VehicleType,
    ServiceVehicleTypeCompatibility,
    InventoryMovement,
    JobOrderService,
    AppointmentService,
    # ... other new models
)
```

### 3.3 Gradually Migrate Models

**Option 1: Create new models alongside old ones** (Less Risky)
- Keep old models running
- Create new enhanced models in `models/enhanced_models.py`
- Update routes gradually to use new models
- Once all routes updated, delete old models

**Option 2: Update in place** (Direct)
- Replace model files with enhanced versions
- Update all route references
- Test thoroughly

---

## Step 4: Update Routes & Controllers

### 4.1 Job Orders - Handling Cancellations

**Before (Old Code):**
```python
@job_orders_bp.route('/job_orders/<int:id>/cancel', methods=['POST'])
def cancel_job_order(id):
    job_order = JobOrder.query.get(id)
    if job_order:
        db.session.delete(job_order)  # WRONG - loses history!
        db.session.commit()
    return redirect(url_for('job_orders.list_job_orders'))
```

**After (New Code):**
```python
@job_orders_bp.route('/job_orders/<int:id>/cancel', methods=['POST'])
def cancel_job_order(id):
    from models.enhanced_models import JobOrder
    job_order = JobOrder.query.get(id)
    
    if job_order:
        reason = request.form.get('cancellation_reason')
        job_order.cancel(reason=reason)
        
        # Handle used inventory (return if possible)
        for item in job_order.items:
            if not item.is_deleted:
                from utils.database_analytics import InventoryAnalytics
                InventoryMovement.record_return(
                    inventory_item_id=item.inventory_item_id,
                    quantity=item.quantity_used,
                    reason='Job order cancelled - parts returned',
                    reference_type='job_order',
                    reference_id=job_order.id,
                    user_id=current_user.id
                )
        
        flash('Job order cancelled successfully', 'success')
    
    return redirect(url_for('job_orders.list_job_orders'))
```

### 4.2 Inventory Usage - Use Movements

**Before (Old Code):**
```python
# Old way - direct deduction
item = InventoryItem.query.get(item_id)
item.stock -= quantity
db.session.commit()
```

**After (New Code):**
```python
# New way - create movement record
from models.enhanced_models import InventoryMovement
movement = InventoryMovement.record_usage(
    inventory_item_id=item_id,
    quantity=quantity,
    reference_type='job_order',
    reference_id=job_order_id,
    reason='Parts used in job order',
    user_id=current_user.id
)
```

### 4.3 Appointments - Vehicle Type Service Selection

**New Route:**
```python
@appointments_bp.route('/appointments/get_services/<int:vehicle_type_id>', methods=['GET'])
def get_applicable_services(vehicle_type_id):
    """Get services applicable to a vehicle type"""
    from utils.database_analytics import VehicleTypeAnalytics
    
    services = VehicleTypeAnalytics.get_services_for_vehicle_type(vehicle_type_id)
    
    return jsonify({
        'services': [s.to_dict() for s in services]
    })
```

**Frontend (appointments.html):**
```javascript
// When vehicle type changes, update available services
document.getElementById('vehicle_type').addEventListener('change', function() {
    const vehicleTypeId = this.value;
    
    fetch(`/appointments/get_services/${vehicleTypeId}`)
        .then(r => r.json())
        .then(data => {
            const serviceCheckboxes = document.getElementById('service_list');
            serviceCheckboxes.innerHTML = '';
            
            data.services.forEach(service => {
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.value = service.id;
                checkbox.name = 'services[]';
                const label = document.createElement('label');
                label.appendChild(checkbox);
                label.appendChild(document.createTextNode(service.name));
                serviceCheckboxes.appendChild(label);
            });
        });
});
```

---

## Step 5: Implement Analytics Routes

### 5.1 Create Analytics Blueprint

**File: `routes/analytics.py`**

```python
from flask import Blueprint, render_template, jsonify, request
from utils.database_analytics import (
    InventoryAnalytics,
    ServiceAnalytics,
    JobOrderAnalytics,
    ReportingHelper
)

analytics_bp = Blueprint('analytics', __name__, url_prefix='/analytics')

@analytics_bp.route('/dashboard', methods=['GET'])
def dashboard():
    """Main analytics dashboard"""
    months = request.args.get('months', 6, type=int)
    data = ReportingHelper.get_analytics_dashboard_data(months)
    return render_template('analytics/dashboard.html', data=data)

@analytics_bp.route('/inventory/forecast', methods=['GET'])
def inventory_forecast():
    """Inventory forecast page"""
    from models.enhanced_models import InventoryItem
    
    items = InventoryItem.query.filter_by(is_active=True).all()
    forecasts = []
    
    for item in items:
        forecast = InventoryAnalytics.forecast_next_month_demand(item.id)
        reorder_point = InventoryAnalytics.calculate_reorder_point(item.id)
        trend = InventoryAnalytics.get_stock_trend_analysis(item.id)
        
        forecasts.append({
            'item': item,
            'forecast': forecast,
            'reorder_point': reorder_point,
            'trend': trend,
            'needs_attention': item.current_stock <= item.reorder_level
        })
    
    return render_template('analytics/inventory_forecast.html', forecasts=forecasts)

@analytics_bp.route('/api/inventory/<int:item_id>/forecast', methods=['GET'])
def api_inventory_forecast(item_id):
    """API endpoint for inventory forecast"""
    months = request.args.get('months', 6, type=int)
    
    from models.enhanced_models import InventoryItem
    item = InventoryItem.query.get(item_id)
    
    if not item:
        return jsonify({'error': 'Item not found'}), 404
    
    forecast = InventoryAnalytics.forecast_next_month_demand(item_id, months)
    monthly_data = InventoryAnalytics.get_monthly_usage(item_id, months)
    
    return jsonify({
        'item': item.to_dict(),
        'forecast': forecast,
        'monthly_data': [
            {
                'month': m[0],
                'quantity': m[1],
                'cost': float(m[2]) if m[2] else 0
            }
            for m in monthly_data
        ]
    })

@analytics_bp.route('/api/services/top', methods=['GET'])
def api_top_services():
    """Get top services"""
    limit = request.args.get('limit', 10, type=int)
    months = request.args.get('months', 6, type=int)
    
    services = ServiceAnalytics.get_top_services(limit, months)
    
    return jsonify({
        'services': [
            {
                'id': s[0],
                'name': s[1],
                'category': s[2],
                'times_requested': s[3],
                'total_revenue': float(s[4]) if s[4] else 0,
                'avg_price': float(s[5]) if s[5] else 0
            }
            for s in services
        ]
    })

@analytics_bp.route('/api/reports/inventory-status', methods=['GET'])
def api_inventory_status():
    """Get inventory status report"""
    from models.enhanced_models import InventoryItem
    
    items_below_reorder = InventoryAnalytics.get_items_below_reorder_level()
    total_items = InventoryItem.query.filter_by(is_active=True).count()
    items_needing_attention = len(items_below_reorder)
    
    return jsonify({
        'total_active_items': total_items,
        'items_needing_reorder': items_needing_attention,
        'percentage': round((items_needing_attention / total_items * 100) if total_items > 0 else 0, 2),
        'items': [
            {
                'id': i[0],
                'name': i[1],
                'sku': i[2],
                'current_stock': i[3],
                'reorder_level': i[4],
                'shortage': i[5],
                'supplier': i[6]
            }
            for i in items_below_reorder
        ]
    })
```

### 5.2 Register Blueprint in App

**File: `app.py`**

```python
from routes.analytics import analytics_bp

app.register_blueprint(analytics_bp)
```

---

## Step 6: Test the Implementation

### 6.1 Unit Tests

**File: `tests/test_inventory_movements.py`**

```python
import unittest
from models.enhanced_models import InventoryMovement, InventoryItem
from app import app, db

class TestInventoryMovements(unittest.TestCase):
    
    def setUp(self):
        self.app = app
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        
        with self.app.app_context():
            db.create_all()
            
            # Create test item
            item = InventoryItem(
                name='Test Oil',
                sku='TEST-OIL-001',
                current_stock=100,
                unit_cost=25.00,
                unit_price=35.00
            )
            db.session.add(item)
            db.session.commit()
            self.item_id = item.id
    
    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
    
    def test_record_usage(self):
        """Test recording stock usage"""
        with self.app.app_context():
            movement = InventoryMovement.record_usage(
                inventory_item_id=self.item_id,
                quantity=10,
                reference_type='job_order',
                reference_id=1,
                reason='Used in job'
            )
            
            item = InventoryItem.query.get(self.item_id)
            self.assertEqual(item.current_stock, 90)
            self.assertEqual(movement.stock_before, 100)
            self.assertEqual(movement.stock_after, 90)
    
    def test_record_receipt(self):
        """Test recording stock receipt"""
        with self.app.app_context():
            movement = InventoryMovement.record_receipt(
                inventory_item_id=self.item_id,
                quantity=50,
                unit_price=25.00,
                purchase_order_num='PO-001',
                reason='Purchase from supplier'
            )
            
            item = InventoryItem.query.get(self.item_id)
            self.assertEqual(item.current_stock, 150)

if __name__ == '__main__':
    unittest.main()
```

### 6.2 Manual Testing Checklist

- [ ] Create a new appointment with vehicle type selection
- [ ] Verify only applicable services are shown
- [ ] Create a job order and add parts
- [ ] Verify inventory movements are recorded
- [ ] Cancel a job order and verify soft delete
- [ ] Check analytics dashboard loads
- [ ] Verify inventory forecast calculations
- [ ] Test reorder point suggestions

---

## Step 7: Data Migration (If Upgrading Existing System)

### 7.1 Backup & Export Current Data

```python
# Script to backup current data
from models import db

def backup_current_data():
    """Export current database to JSON for backup"""
    import json
    from datetime import datetime
    
    backup = {
        'timestamp': datetime.utcnow().isoformat(),
        'data': {}
    }
    
    # Export all tables...
    # (See models for what to export)
    
    with open(f'backup_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json', 'w') as f:
        json.dump(backup, f, indent=2, default=str)
```

### 7.2 Migrate to New Structure

```python
def migrate_to_new_schema():
    """Migrate from old schema to new schema"""
    from models.service import Service as OldService
    from models.enhanced_models import Service as NewService
    from models import db
    
    # 1. Copy services
    old_services = OldService.query.all()
    for old_svc in old_services:
        new_svc = NewService(
            name=old_svc.name,
            description=old_svc.description,
            category=old_svc.category,
            icon=old_svc.icon,
            base_price=old_svc.price,
            estimated_duration_minutes=old_svc.duration_minutes,
            is_active=old_svc.is_active
        )
        db.session.add(new_svc)
    db.session.commit()
    
    print("✓ Services migrated")
    
    # 2. Add soft delete fields (other tables)
    # ...
```

---

## Step 8: Update Requirements

**File: `requirements.txt`**

Add if not present:
```
Flask-Migrate>=3.0.0
SQLAlchemy>=1.4.0
```

---

## Step 9: Deployment Checklist

- [ ] All models updated and tested locally
- [ ] Database migrations created and tested
- [ ] Routes updated to use new models
- [ ] Analytics endpoints working
- [ ] Soft delete methods tested
- [ ] Inventory movement recording verified
- [ ] Backup of production database created
- [ ] Analytics dashboard pages created
- [ ] User documentation updated

---

## Step 10: Important Reminders

### Never in Production:
- ❌ `DELETE FROM <transactional_table>` - Always use soft delete
- ❌ Direct `UPDATE inventory_items SET stock = ...` - Use movements
- ❌ Bypass audit/tracking - Always record reason for changes

### Always in Production:
- ✅ Test on staging first
- ✅ Maintain backups before schema changes
- ✅ Use soft deletes for transactional data
- ✅ Record movement reasons
- ✅ Verify stock before/after balances
- ✅ Run analytics periodically

---

## Troubleshooting

### Issue: Foreign Key Constraint Errors

**Solution**: Ensure all referenced records exist
```python
# Check before deleting
if JobOrder.query.filter_by(job_order_id=id).count() > 0:
    raise ValueError("Cannot delete - has related job orders")
```

### Issue: Stock Mismatch After Migration

**Solution**: Recalculate from movements
```python
def recalculate_stock():
    """Recalculate current stock from movements"""
    items = InventoryItem.query.all()
    
    for item in items:
        total_in = db.session.query(
            func.sum(InventoryMovement.quantity)
        ).filter(
            InventoryMovement.inventory_item_id == item.id,
            InventoryMovement.movement_type.in_(['IN', 'RETURN'])
        ).scalar() or 0
        
        total_out = db.session.query(
            func.sum(InventoryMovement.quantity)
        ).filter(
            InventoryMovement.inventory_item_id == item.id,
            InventoryMovement.movement_type.in_(['OUT', 'WASTAGE'])
        ).scalar() or 0
        
        item.current_stock = total_in - total_out
    
    db.session.commit()
```

### Issue: Analytics Dashboard Slow

**Solution**: Add database indexes
```python
# In migration file
db.session.execute('''
    CREATE INDEX idx_movement_date_item 
    ON inventory_movements(inventory_item_id, movement_date);
    
    CREATE INDEX idx_joborder_created 
    ON job_orders(created_at);
''')
```

---

## Success Indicators

✅ All models instantiate without errors
✅ Soft delete methods work as expected
✅ Inventory movements track correctly
✅ Analytics queries return data
✅ Forecasts generate predictions
✅ Vehicle type services filter properly
✅ No hard deletes on transactional data
✅ Dashboard loads in <2 seconds

Proceed when all items are checked!
