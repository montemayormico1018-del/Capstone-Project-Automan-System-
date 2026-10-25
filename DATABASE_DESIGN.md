# Car Services Management System - Database Design & Architecture

## Executive Summary

This document provides a complete database design for a Web-Based Car Services Job Order Tracking, Inventory, and Sales Management System with Predictive Analytics.

**Key Principles:**
- **Master Data**: Full CRUD operations with hard delete capability
- **Transactional Data**: Soft deletes only (no hard deletion), historical preservation for analytics
- **Data Integrity**: Foreign key constraints, cascading actions, audit trails
- **Analytics-Ready**: Historical tracking for forecasting (3-6 months minimum)

---

## Part 1: Data Classification

### Master Data Tables (Full CRUD + Hard Delete)
```
car_brands
vehicle_types
services
inventory_items
users
customers
```

### Transactional Data Tables (Soft Delete Only, NO Hard Deletes)
```
vehicles
appointments
job_orders
job_order_items
sales
sale_items
inventory_movements
```

### Analytics/Audit Tables
```
inventory_usage_logs (auto-populated from inventory_movements)
service_demand_logs (auto-populated from job_order_items)
forecast_logs (calculated analytics)
audit_logs (optional, for compliance)
```

---

## Part 2: Complete SQL Table Structure

### A. MASTER DATA TABLES

#### 1. Car Brands
```sql
CREATE TABLE car_brands (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL UNIQUE,
    country VARCHAR(50),
    logo_url VARCHAR(255),
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_name (name),
    INDEX idx_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 2. Vehicle Types (Master data for powertrains)
```sql
CREATE TABLE vehicle_types (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    type_name VARCHAR(50) NOT NULL UNIQUE,
    description TEXT,
    -- Type: Gasoline, Diesel, HEV, PHEV, BEV
    fuel_type ENUM('Gasoline', 'Diesel', 'HEV', 'PHEV', 'BEV') NOT NULL,
    
    -- Service applicability flags
    requires_oil_change BOOLEAN DEFAULT TRUE,
    requires_transmission_service BOOLEAN DEFAULT TRUE,
    requires_exhaust_inspection BOOLEAN DEFAULT TRUE,
    requires_battery_inspection BOOLEAN DEFAULT FALSE,
    requires_coolant_flush BOOLEAN DEFAULT TRUE,
    
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_fuel_type (fuel_type),
    INDEX idx_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 3. Services (Master data)
```sql
CREATE TABLE services (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(120) NOT NULL,
    description TEXT,
    category VARCHAR(50),  -- maintenance, repair, inspection, diagnostics
    icon VARCHAR(50),      -- Bootstrap icon class
    base_price DECIMAL(10, 2) NOT NULL,
    estimated_duration_minutes INTEGER,
    
    -- Service applicability
    parent_service_id INTEGER,
    is_sub_service BOOLEAN DEFAULT FALSE,
    
    -- Vehicle type applicability
    applicable_to_all BOOLEAN DEFAULT TRUE,
    
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (parent_service_id) REFERENCES services(id) ON DELETE SET NULL,
    INDEX idx_category (category),
    INDEX idx_is_active (is_active),
    INDEX idx_parent_service_id (parent_service_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 4. Service-Vehicle Type Compatibility (Junction table for master data)
```sql
CREATE TABLE service_vehicle_type_compatibility (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    service_id INTEGER NOT NULL,
    vehicle_type_id INTEGER NOT NULL,
    is_applicable BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY unique_service_vehicle (service_id, vehicle_type_id),
    FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE CASCADE,
    FOREIGN KEY (vehicle_type_id) REFERENCES vehicle_types(id) ON DELETE CASCADE,
    INDEX idx_vehicle_type_id (vehicle_type_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 5. Inventory Items (Master data)
```sql
CREATE TABLE inventory_items (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(120) NOT NULL,
    sku VARCHAR(50) NOT NULL UNIQUE,
    description TEXT,
    category VARCHAR(50),  -- oil, tires, brakes, battery, etc.
    
    -- Stock info (current balance)
    current_stock INTEGER DEFAULT 0,
    reorder_level INTEGER DEFAULT 10,
    reorder_quantity INTEGER DEFAULT 50,
    
    -- Pricing
    unit_cost DECIMAL(10, 2) NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    
    -- Supplier info
    supplier VARCHAR(120),
    supplier_sku VARCHAR(50),
    lead_time_days INTEGER,
    
    -- Storage
    storage_location VARCHAR(50),
    bin_location VARCHAR(50),
    
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_sku (sku),
    INDEX idx_category (category),
    INDEX idx_is_active (is_active),
    INDEX idx_reorder_level (current_stock, reorder_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 6. Users (Master data - employees, mechanics, staff)
```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(120) NOT NULL,
    email VARCHAR(120) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    phone VARCHAR(20),
    
    role VARCHAR(50) NOT NULL,  -- admin, manager, mechanic, staff, customer_service
    department VARCHAR(50),
    
    is_active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMP NULL,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_email (email),
    INDEX idx_role (role),
    INDEX idx_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 7. Customers (Master data - customer contacts)
```sql
CREATE TABLE customers (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(120) NOT NULL,
    email VARCHAR(120),
    phone VARCHAR(20),
    address TEXT,
    city VARCHAR(50),
    province VARCHAR(50),
    postal_code VARCHAR(10),
    
    contact_preference VARCHAR(20),  -- phone, email, sms
    
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_email (email),
    INDEX idx_phone (phone),
    INDEX idx_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

---

### B. TRANSACTIONAL DATA TABLES (Soft Delete)

#### 8. Vehicles (Soft Delete - customer vehicles)
```sql
CREATE TABLE vehicles (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    customer_id INTEGER NOT NULL,
    car_brand_id INTEGER NOT NULL,
    vehicle_type_id INTEGER NOT NULL,
    
    model VARCHAR(100) NOT NULL,
    year INTEGER,
    license_plate VARCHAR(20) NOT NULL,
    vin VARCHAR(50),
    color VARCHAR(30),
    
    current_mileage INTEGER DEFAULT 0,
    mileage_unit VARCHAR(10) DEFAULT 'KM',  -- KM or MI
    
    notes TEXT,
    image_url VARCHAR(255),
    
    -- Soft delete
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMP NULL,
    deletion_reason VARCHAR(255),
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE RESTRICT,
    FOREIGN KEY (car_brand_id) REFERENCES car_brands(id) ON DELETE RESTRICT,
    FOREIGN KEY (vehicle_type_id) REFERENCES vehicle_types(id) ON DELETE RESTRICT,
    
    UNIQUE KEY unique_license_plate (license_plate),
    INDEX idx_customer_id (customer_id),
    INDEX idx_is_deleted (is_deleted),
    INDEX idx_vehicle_type_id (vehicle_type_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 9. Appointments (Soft Delete)
```sql
CREATE TABLE appointments (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    appointment_number VARCHAR(50) NOT NULL UNIQUE,
    
    customer_id INTEGER NOT NULL,
    vehicle_id INTEGER,
    
    -- Vehicle info (may be entered without permanent vehicle record)
    vehicle_make VARCHAR(50),
    vehicle_model VARCHAR(50),
    license_plate_input VARCHAR(20),
    year_of_vehicle INTEGER,
    
    -- Appointment details
    preferred_date DATE NOT NULL,
    preferred_time TIME NOT NULL,
    notes TEXT,
    
    -- Status tracking
    status VARCHAR(20) DEFAULT 'Pending',
    -- Pending, Quotation Sent, Approved, In Progress, 
    -- Completed, Rejected, Cancelled, No-Show
    
    service_estimate DECIMAL(10, 2),
    assigned_to_user_id INTEGER,
    
    -- Soft delete
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMP NULL,
    cancellation_reason VARCHAR(255),
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE RESTRICT,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE SET NULL,
    FOREIGN KEY (assigned_to_user_id) REFERENCES users(id) ON DELETE SET NULL,
    
    UNIQUE KEY unique_appointment_number (appointment_number),
    INDEX idx_customer_id (customer_id),
    INDEX idx_status (status),
    INDEX idx_preferred_date (preferred_date),
    INDEX idx_is_deleted (is_deleted)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 10. Appointment Services (Junction table)
```sql
CREATE TABLE appointment_services (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    appointment_id INTEGER NOT NULL,
    service_id INTEGER NOT NULL,
    
    quoted_price DECIMAL(10, 2),
    is_approved BOOLEAN DEFAULT FALSE,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY unique_appointment_service (appointment_id, service_id),
    FOREIGN KEY (appointment_id) REFERENCES appointments(id) ON DELETE CASCADE,
    FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE RESTRICT,
    
    INDEX idx_service_id (service_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 11. Job Orders (Soft Delete - main transactional record)
```sql
CREATE TABLE job_orders (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    job_order_number VARCHAR(50) NOT NULL UNIQUE,
    
    appointment_id INTEGER,
    customer_id INTEGER NOT NULL,
    vehicle_id INTEGER NOT NULL,
    assigned_mechanic_id INTEGER,
    
    -- Job details
    diagnosis TEXT,
    work_description TEXT,
    recommendations TEXT,
    mechanic_notes TEXT,
    
    -- Status tracking
    status VARCHAR(20) DEFAULT 'Pending',
    -- Pending, In Progress, On Hold, Completed, Cancelled, Voided
    
    priority VARCHAR(20) DEFAULT 'Normal',  -- Low, Normal, High, Urgent
    
    -- Costs
    labor_cost DECIMAL(10, 2) DEFAULT 0.00,
    parts_cost DECIMAL(10, 2) DEFAULT 0.00,
    total_cost DECIMAL(10, 2) DEFAULT 0.00,
    
    -- Timing
    estimated_completion TIMESTAMP,
    started_at TIMESTAMP NULL,
    completed_at TIMESTAMP NULL,
    
    -- Soft delete
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMP NULL,
    void_reason VARCHAR(255),
    is_voided BOOLEAN DEFAULT FALSE,
    voided_at TIMESTAMP NULL,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (appointment_id) REFERENCES appointments(id) ON DELETE SET NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE RESTRICT,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE RESTRICT,
    FOREIGN KEY (assigned_mechanic_id) REFERENCES users(id) ON DELETE SET NULL,
    
    UNIQUE KEY unique_job_order_number (job_order_number),
    INDEX idx_job_order_number (job_order_number),
    INDEX idx_status (status),
    INDEX idx_is_voided (is_voided),
    INDEX idx_is_deleted (is_deleted),
    INDEX idx_customer_id (customer_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 12. Job Order Items (Parts used in job order)
```sql
CREATE TABLE job_order_items (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    job_order_id INTEGER NOT NULL,
    inventory_item_id INTEGER,
    
    -- What was used (snapshot for historical accuracy)
    item_name VARCHAR(120) NOT NULL,
    item_sku VARCHAR(50),
    
    quantity_used INTEGER NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    total_price DECIMAL(10, 2) GENERATED ALWAYS AS (quantity_used * unit_price) STORED,
    
    -- Item classification
    is_service BOOLEAN DEFAULT FALSE,  -- If TRUE, this is a service charge
    service_id INTEGER,
    
    -- Notes
    notes TEXT,
    
    -- Soft delete
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMP NULL,
    deletion_reason VARCHAR(255),
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (job_order_id) REFERENCES job_orders(id) ON DELETE CASCADE,
    FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id) ON DELETE SET NULL,
    FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE SET NULL,
    
    INDEX idx_job_order_id (job_order_id),
    INDEX idx_inventory_item_id (inventory_item_id),
    INDEX idx_is_deleted (is_deleted)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 13. Job Order Services (Services performed on job order)
```sql
CREATE TABLE job_order_services (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    job_order_id INTEGER NOT NULL,
    service_id INTEGER NOT NULL,
    
    service_name VARCHAR(120),  -- Snapshot
    quoted_price DECIMAL(10, 2),
    actual_price DECIMAL(10, 2),
    
    is_completed BOOLEAN DEFAULT FALSE,
    completion_notes TEXT,
    
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMP NULL,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY unique_job_service (job_order_id, service_id),
    FOREIGN KEY (job_order_id) REFERENCES job_orders(id) ON DELETE CASCADE,
    FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE RESTRICT,
    
    INDEX idx_service_id (service_id),
    INDEX idx_is_completed (is_completed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 14. Inventory Movements (Stock IN/OUT/RETURN/ADJUSTMENT - Core for Analytics)
```sql
CREATE TABLE inventory_movements (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    movement_number VARCHAR(50) NOT NULL UNIQUE,
    
    inventory_item_id INTEGER NOT NULL,
    
    -- Movement type
    movement_type ENUM('IN', 'OUT', 'RETURN', 'ADJUSTMENT', 'WASTAGE') NOT NULL,
    
    -- Quantity and pricing
    quantity INTEGER NOT NULL,
    unit_price DECIMAL(10, 2),
    total_value DECIMAL(10, 2) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    
    -- Reference to transaction
    reference_type VARCHAR(50),  -- job_order, sale, purchase, adjustment
    reference_id INTEGER,
    purchase_order_number VARCHAR(50),
    
    -- Movement reason
    reason VARCHAR(255),
    notes TEXT,
    
    -- Status
    status VARCHAR(20) DEFAULT 'Completed',  -- Pending, Completed, Cancelled, Reversed
    is_cancelled BOOLEAN DEFAULT FALSE,
    cancellation_reason VARCHAR(255),
    
    -- Stock balance after movement
    stock_before INTEGER,
    stock_after INTEGER,
    
    -- Recording
    recorded_by_user_id INTEGER,
    verified_by_user_id INTEGER,
    verified_at TIMESTAMP NULL,
    
    -- Timestamps
    movement_date DATE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id) ON DELETE RESTRICT,
    FOREIGN KEY (recorded_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (verified_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
    
    UNIQUE KEY unique_movement_number (movement_number),
    INDEX idx_inventory_item_id (inventory_item_id),
    INDEX idx_movement_type (movement_type),
    INDEX idx_movement_date (movement_date),
    INDEX idx_status (status),
    INDEX idx_reference (reference_type, reference_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 15. Sales (Soft Delete)
```sql
CREATE TABLE sales (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    sales_number VARCHAR(50) NOT NULL UNIQUE,
    invoice_number VARCHAR(50) NOT NULL UNIQUE,
    
    job_order_id INTEGER,
    customer_id INTEGER NOT NULL,
    vehicle_id INTEGER,
    
    -- Financial
    subtotal DECIMAL(10, 2) NOT NULL,
    tax DECIMAL(10, 2) DEFAULT 0.00,
    discount DECIMAL(10, 2) DEFAULT 0.00,
    total DECIMAL(10, 2) NOT NULL,
    
    -- Payment
    payment_status VARCHAR(20) DEFAULT 'Pending',
    -- Pending, Partial, Paid, Refunded, Cancelled
    
    payment_method VARCHAR(50),  -- Cash, Card, Check, Bank Transfer
    amount_paid DECIMAL(10, 2) DEFAULT 0.00,
    payment_date TIMESTAMP NULL,
    
    -- Notes
    notes TEXT,
    internal_notes TEXT,
    
    -- Soft delete / Cancellation
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMP NULL,
    
    is_cancelled BOOLEAN DEFAULT FALSE,
    cancelled_at TIMESTAMP NULL,
    cancellation_reason VARCHAR(255),
    
    -- Refund tracking
    is_refunded BOOLEAN DEFAULT FALSE,
    refund_amount DECIMAL(10, 2) DEFAULT 0.00,
    refund_date TIMESTAMP NULL,
    refund_reason VARCHAR(255),
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (job_order_id) REFERENCES job_orders(id) ON DELETE SET NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE RESTRICT,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE SET NULL,
    
    UNIQUE KEY unique_invoice_number (invoice_number),
    UNIQUE KEY unique_sales_number (sales_number),
    INDEX idx_payment_status (payment_status),
    INDEX idx_is_deleted (is_deleted),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 16. Sale Items (Line items in sales)
```sql
CREATE TABLE sale_items (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    sale_id INTEGER NOT NULL,
    
    -- Item reference
    inventory_item_id INTEGER,
    job_order_item_id INTEGER,
    
    -- Item snapshot
    item_name VARCHAR(120) NOT NULL,
    item_description TEXT,
    
    -- Quantity and pricing
    quantity INTEGER NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    total_price DECIMAL(10, 2) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    
    -- Notes
    notes TEXT,
    
    -- Soft delete
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMP NULL,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE,
    FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id) ON DELETE SET NULL,
    FOREIGN KEY (job_order_item_id) REFERENCES job_order_items(id) ON DELETE SET NULL,
    
    INDEX idx_sale_id (sale_id),
    INDEX idx_inventory_item_id (inventory_item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

---

### C. ANALYTICS & AUDIT TABLES

#### 17. Inventory Usage Logs (Auto-populated from inventory_movements)
```sql
CREATE TABLE inventory_usage_logs (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    
    inventory_item_id INTEGER NOT NULL,
    item_name VARCHAR(120),
    item_sku VARCHAR(50),
    item_category VARCHAR(50),
    
    -- Usage in this period
    quantity_used INTEGER DEFAULT 0,
    total_cost DECIMAL(10, 2) DEFAULT 0.00,
    
    -- Period
    usage_date DATE NOT NULL,
    month_year VARCHAR(10),
    
    -- Reference
    reference_type VARCHAR(50),  -- job_order, sale, wastage, adjustment
    
    -- Status flags
    is_valid BOOLEAN DEFAULT TRUE,  -- Exclude cancelled/voided entries
    status VARCHAR(20),  -- Used, Returned, Voided
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id) ON DELETE RESTRICT,
    
    INDEX idx_inventory_item_id (inventory_item_id),
    INDEX idx_usage_date (usage_date),
    INDEX idx_month_year (month_year),
    INDEX idx_is_valid (is_valid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 18. Service Demand Logs (Auto-populated from job order services)
```sql
CREATE TABLE service_demand_logs (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    
    service_id INTEGER NOT NULL,
    service_name VARCHAR(120),
    service_category VARCHAR(50),
    
    -- Demand in this period
    quantity_demanded INTEGER DEFAULT 0,
    total_revenue DECIMAL(10, 2) DEFAULT 0.00,
    
    -- Period
    demand_date DATE NOT NULL,
    month_year VARCHAR(10),
    
    -- Status flags
    is_valid BOOLEAN DEFAULT TRUE,  -- Exclude cancelled/voided entries
    status VARCHAR(20),  -- Completed, Cancelled, Voided
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE RESTRICT,
    
    INDEX idx_service_id (service_id),
    INDEX idx_demand_date (demand_date),
    INDEX idx_month_year (month_year),
    INDEX idx_is_valid (is_valid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 19. Forecast Logs (Predictive analytics results)
```sql
CREATE TABLE forecast_logs (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    
    -- Item being forecasted
    item_type VARCHAR(50) NOT NULL,  -- inventory or service
    item_id INTEGER NOT NULL,
    item_name VARCHAR(120),
    
    -- Forecast
    forecast_type VARCHAR(50),  -- demand, reorder, trend, anomaly
    
    -- Prediction
    predicted_value DECIMAL(12, 2),
    predicted_quantity INTEGER,
    confidence_percentage DECIMAL(5, 2),
    
    -- Analysis period
    analysis_start_date DATE,
    analysis_end_date DATE,
    forecast_period VARCHAR(20),  -- next_month, next_quarter, etc.
    
    -- Supporting data
    historical_average DECIMAL(12, 2),
    historical_stddev DECIMAL(12, 2),
    trend_direction VARCHAR(20),  -- up, down, stable
    
    -- Recommendations
    recommendation TEXT,
    suggested_action VARCHAR(255),
    
    -- Status
    status VARCHAR(20),  -- active, archived, superseded
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_item_type_id (item_type, item_id),
    INDEX idx_forecast_type (forecast_type),
    INDEX idx_analysis_end_date (analysis_end_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 20. Audit Logs (Optional - for compliance)
```sql
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    
    -- What changed
    table_name VARCHAR(50) NOT NULL,
    record_id INTEGER NOT NULL,
    action VARCHAR(20) NOT NULL,  -- CREATE, UPDATE, DELETE, SOFT_DELETE, RESTORE
    
    -- Who and when
    user_id INTEGER,
    performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- What changed
    old_values JSON,
    new_values JSON,
    
    -- Context
    ip_address VARCHAR(45),
    user_agent VARCHAR(255),
    notes TEXT,
    
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    
    INDEX idx_table_name (table_name),
    INDEX idx_action (action),
    INDEX idx_performed_at (performed_at),
    INDEX idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

---

## Part 3: Key Queries

### A. SOFT DELETE OPERATIONS

#### Soft Delete Transaction Record
```sql
-- Soft delete an appointment
UPDATE appointments
SET is_deleted = TRUE,
    deleted_at = CURRENT_TIMESTAMP,
    cancellation_reason = 'Customer requested cancellation'
WHERE id = 123;
```

#### Restore Soft Deleted Record
```sql
-- Restore a deleted appointment
UPDATE appointments
SET is_deleted = FALSE,
    deleted_at = NULL,
    cancellation_reason = NULL
WHERE id = 123;
```

#### Void/Cancel Job Order (with reason tracking)
```sql
-- Void a job order due to parts unavailability
UPDATE job_orders
SET status = 'Cancelled',
    is_voided = TRUE,
    voided_at = CURRENT_TIMESTAMP,
    void_reason = 'Parts not available, ordered via alternate supplier'
WHERE id = 456;
```

#### Record Item Adjustment (Return/Correction)
```sql
-- Record a parts return due to installation error
INSERT INTO inventory_movements (
    movement_number,
    inventory_item_id,
    movement_type,
    quantity,
    unit_price,
    reference_type,
    reference_id,
    reason,
    notes,
    status,
    stock_before,
    stock_after,
    recorded_by_user_id,
    movement_date
) VALUES (
    'MOV-RET-20260417-001',
    42,
    'RETURN',
    2,
    35.50,
    'job_order',
    456,
    'Parts damaged during installation, replaced under warranty',
    'Damaged brake pads - customer not charged',
    'Completed',
    18,
    20,
    1,
    CURDATE()
);
```

---

### B. FILTERING VALID RECORDS FOR ANALYTICS

#### Get All Active (Valid) Inventory Movements
```sql
SELECT *
FROM inventory_movements
WHERE is_cancelled = FALSE
AND status IN ('Completed', 'Verified')
AND movement_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
ORDER BY movement_date DESC;
```

#### Get Valid Job Orders for Analytics
```sql
SELECT jo.*
FROM job_orders jo
WHERE jo.is_deleted = FALSE
AND jo.is_voided = FALSE
AND jo.status IN ('Completed', 'In Progress')
AND jo.created_at >= DATE_SUB(NOW(), INTERVAL 6 MONTH)
ORDER BY jo.created_at DESC;
```

#### Get Completed, Non-Cancelled Sales
```sql
SELECT s.*
FROM sales s
WHERE s.is_deleted = FALSE
AND s.is_cancelled = FALSE
AND s.payment_status IN ('Paid', 'Partial')
AND s.created_at >= DATE_SUB(NOW(), INTERVAL 3 MONTH)
ORDER BY s.created_at DESC;
```

---

### C. INVENTORY USAGE & MOVEMENT QUERIES

#### Calculate Total Usage Per Item (Last 3 Months)
```sql
SELECT
    ii.id,
    ii.name,
    ii.sku,
    ii.category,
    ii.current_stock,
    ii.reorder_level,
    SUM(CASE WHEN im.movement_type = 'OUT' THEN im.quantity ELSE 0 END) as total_used,
    SUM(CASE WHEN im.movement_type = 'IN' THEN im.quantity ELSE 0 END) as total_received,
    SUM(CASE WHEN im.movement_type = 'RETURN' THEN im.quantity ELSE 0 END) as total_returned,
    SUM(
        CASE 
            WHEN im.movement_type = 'OUT' THEN (im.quantity * im.unit_price)
            ELSE 0 
        END
    ) as total_cost_consumed,
    COUNT(DISTINCT im.id) as movement_count
FROM inventory_items ii
LEFT JOIN inventory_movements im ON ii.id = im.inventory_item_id
    AND im.is_cancelled = FALSE
    AND im.status IN ('Completed', 'Verified')
    AND im.movement_date >= DATE_SUB(CURDATE(), INTERVAL 3 MONTH)
WHERE ii.is_active = TRUE
GROUP BY ii.id, ii.name, ii.sku, ii.category, ii.current_stock, ii.reorder_level
ORDER BY total_used DESC;
```

#### Identify Items Below Reorder Level
```sql
SELECT
    ii.id,
    ii.name,
    ii.sku,
    ii.current_stock,
    ii.reorder_level,
    (ii.reorder_level - ii.current_stock) as shortage_quantity,
    ii.reorder_quantity,
    ii.unit_cost,
    (ii.reorder_quantity * ii.unit_cost) as estimated_order_cost,
    ii.supplier,
    ii.lead_time_days
FROM inventory_items ii
WHERE ii.is_active = TRUE
AND ii.current_stock <= ii.reorder_level
ORDER BY shortage_quantity DESC, ii.name;
```

#### Monthly Inventory Usage Summary
```sql
SELECT
    DATE_FORMAT(im.movement_date, '%Y-%m') as month,
    ii.category,
    COUNT(DISTINCT ii.id) as unique_items,
    SUM(CASE WHEN im.movement_type = 'OUT' THEN im.quantity ELSE 0 END) as items_used,
    SUM(CASE WHEN im.movement_type = 'OUT' THEN (im.quantity * im.unit_price) ELSE 0 END) as cost_used,
    SUM(CASE WHEN im.movement_type = 'IN' THEN im.quantity ELSE 0 END) as items_received,
    SUM(CASE WHEN im.movement_type = 'RETURN' THEN im.quantity ELSE 0 END) as items_returned,
    SUM(CASE WHEN im.movement_type = 'WASTAGE' THEN im.quantity ELSE 0 END) as items_wasted
FROM inventory_movements im
JOIN inventory_items ii ON im.inventory_item_id = ii.id
WHERE im.is_cancelled = FALSE
AND im.status IN ('Completed', 'Verified')
AND im.movement_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
GROUP BY DATE_FORMAT(im.movement_date, '%Y-%m'), ii.category
ORDER BY month DESC, category;
```

#### Track Stock History for Specific Item
```sql
SELECT
    im.movement_number,
    im.movement_type,
    im.quantity,
    im.stock_before,
    im.stock_after,
    im.reference_type,
    im.reference_id,
    im.reason,
    im.movement_date,
    u.name as recorded_by,
    im.notes
FROM inventory_movements im
LEFT JOIN users u ON im.recorded_by_user_id = u.id
WHERE im.inventory_item_id = 42
AND im.is_cancelled = FALSE
ORDER BY im.movement_date DESC, im.created_at DESC
LIMIT 100;
```

---

### D. PREDICTIVE ANALYTICS QUERIES

#### Calculate Average Monthly Demand (Last 6 Months)
```sql
SELECT
    ii.id,
    ii.name,
    ii.sku,
    ii.category,
    COUNT(DISTINCT DATE_FORMAT(im.movement_date, '%Y-%m')) as months_active,
    SUM(CASE WHEN im.movement_type = 'OUT' THEN im.quantity ELSE 0 END) as total_used_6m,
    ROUND(
        SUM(CASE WHEN im.movement_type = 'OUT' THEN im.quantity ELSE 0 END) / 6,
        2
    ) as avg_monthly_demand,
    ROUND(
        SUM(CASE WHEN im.movement_type = 'OUT' THEN (im.quantity * im.unit_price) ELSE 0 END) / 6,
        2
    ) as avg_monthly_cost,
    ii.reorder_level,
    ii.lead_time_days,
    -- Suggested reorder point = (avg_monthly * lead_time_days/30) + safety_stock
    ROUND(
        (SUM(CASE WHEN im.movement_type = 'OUT' THEN im.quantity ELSE 0 END) / 6) 
        * (ii.lead_time_days / 30.0) 
        + (SUM(CASE WHEN im.movement_type = 'OUT' THEN im.quantity ELSE 0 END) / 6 * 0.5),
        0
    ) as suggested_reorder_point
FROM inventory_items ii
LEFT JOIN inventory_movements im ON ii.id = im.inventory_item_id
    AND im.is_cancelled = FALSE
    AND im.status IN ('Completed', 'Verified')
    AND im.movement_type = 'OUT'
    AND im.movement_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
WHERE ii.is_active = TRUE
GROUP BY ii.id, ii.name, ii.sku, ii.category, ii.reorder_level, ii.lead_time_days
ORDER BY avg_monthly_demand DESC;
```

#### Forecast Next Month's Demand
```sql
-- Simple trend-based forecast for next month
SELECT
    ii.id,
    ii.name,
    ii.sku,
    ROUND(AVG(monthly_usage.month_qty), 0) as avg_monthly_usage,
    ROUND(STDDEV(monthly_usage.month_qty), 2) as usage_stddev,
    ROUND(AVG(monthly_usage.month_qty) + (STDDEV(monthly_usage.month_qty) * 0.5), 0) as forecasted_demand,
    ROUND(AVG(monthly_usage.month_cost), 2) as avg_monthly_cost,
    DATE_ADD(CURDATE(), INTERVAL 1 MONTH) as forecast_period_start
FROM inventory_items ii
LEFT JOIN (
    SELECT
        im.inventory_item_id,
        DATE_FORMAT(im.movement_date, '%Y-%m') as month,
        SUM(CASE WHEN im.movement_type = 'OUT' THEN im.quantity ELSE 0 END) as month_qty,
        SUM(CASE WHEN im.movement_type = 'OUT' THEN (im.quantity * im.unit_price) ELSE 0 END) as month_cost
    FROM inventory_movements im
    WHERE im.is_cancelled = FALSE
    AND im.status IN ('Completed', 'Verified')
    AND im.movement_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
    GROUP BY im.inventory_item_id, DATE_FORMAT(im.movement_date, '%Y-%m')
) monthly_usage ON ii.id = monthly_usage.inventory_item_id
WHERE ii.is_active = TRUE
GROUP BY ii.id, ii.name, ii.sku
HAVING COUNT(monthly_usage.month) >= 3
ORDER BY forecasted_demand DESC;
```

---

### E. VEHICLE TYPE-BASED SERVICE QUERIES

#### Get Applicable Services for a Vehicle Type
```sql
SELECT
    s.id,
    s.name,
    s.description,
    s.base_price,
    s.estimated_duration_minutes,
    s.category,
    vt.type_name,
    COALESCE(svc.is_applicable, s.applicable_to_all) as is_applicable
FROM services s
LEFT JOIN service_vehicle_type_compatibility svc 
    ON s.id = svc.service_id 
    AND svc.vehicle_type_id = 3  -- BEV example
LEFT JOIN vehicle_types vt ON vt.id = 3
WHERE s.is_active = TRUE
AND (s.applicable_to_all = TRUE 
    OR svc.is_applicable = TRUE
    OR svc.id IS NULL)
ORDER BY s.category, s.name;
```

#### Exclude Oil Change for BEV Vehicles
```sql
-- Query that filters out inappropriate services
SELECT
    s.id,
    s.name,
    s.base_price
FROM services s
WHERE s.is_active = TRUE
AND NOT (
    -- Exclude oil-related services for BEV
    s.name LIKE '%Oil Change%'
    OR s.name LIKE '%Transmission Fluid%'
    OR s.name LIKE '%Engine Flush%'
)
AND EXISTS (
    SELECT 1
    FROM service_vehicle_type_compatibility svc
    WHERE svc.service_id = s.id
    AND svc.vehicle_type_id = (
        SELECT id FROM vehicle_types WHERE fuel_type = 'BEV'
    )
    AND svc.is_applicable = TRUE
)
ORDER BY s.name;
```

#### Create Appointment with BEV-Appropriate Services Only
```sql
-- Suggest services based on vehicle type
SELECT
    s.id,
    s.name,
    s.base_price,
    s.estimated_duration_minutes,
    vt.fuel_type,
    -- Check if applicable
    CASE 
        WHEN s.applicable_to_all THEN TRUE
        WHEN vt.fuel_type = 'BEV' AND s.name NOT LIKE '%Oil%' THEN TRUE
        ELSE COALESCE(svc.is_applicable, FALSE)
    END as is_applicable_to_vehicle
FROM services s
LEFT JOIN service_vehicle_type_compatibility svc ON s.id = svc.service_id
CROSS JOIN vehicle_types vt
WHERE s.is_active = TRUE
AND vt.id = 3  -- BEV vehicle type
AND (
    s.applicable_to_all = TRUE
    OR (vt.fuel_type = 'BEV' AND s.name NOT LIKE '%Oil%')
    OR svc.is_applicable = TRUE
)
ORDER BY s.category, s.name;
```

---

## Part 4: Implementation Guidelines

### A. Preventing Common Mistakes

#### ❌ DON'T: Use DELETE on transactional data
```sql
-- WRONG - This loses historical data
DELETE FROM job_orders WHERE id = 123;
```

#### ✅ DO: Use soft delete with status tracking
```sql
-- CORRECT - Preserves history
UPDATE job_orders
SET is_deleted = TRUE,
    deleted_at = CURRENT_TIMESTAMP,
    void_reason = 'Duplicate order'
WHERE id = 123;
```

#### ❌ DON'T: Modify stock directly
```sql
-- WRONG - No audit trail
UPDATE inventory_items 
SET current_stock = current_stock - 5 
WHERE id = 42;
```

#### ✅ DO: Record inventory movements
```sql
-- CORRECT - Creates audit trail
INSERT INTO inventory_movements (
    movement_number,
    inventory_item_id,
    movement_type,
    quantity,
    reference_type,
    reference_id,
    stock_before,
    stock_after,
    movement_date
) VALUES (
    'MOV-OUT-20260417-001',
    42,
    'OUT',
    5,
    'job_order',
    456,
    10,
    5,
    CURDATE()
);
UPDATE inventory_items SET current_stock = 5 WHERE id = 42;
```

---

### B. Best Practices for Data Integrity

#### 1. Always Include Reason Tracking
```sql
-- For any deletion, cancellation, or return:
UPDATE appointments
SET is_deleted = TRUE,
    deleted_at = CURRENT_TIMESTAMP,
    cancellation_reason = 'Customer called to reschedule'
WHERE id = 789;
```

#### 2. Use Status Enums to Control Workflow
```sql
-- Define allowed statuses and transitions in application logic
job_order_status: 'Pending' → 'In Progress' → 'On Hold' OR 'Completed'
                   ↓
                 'Cancelled' (with reason)
                   ↓
                 'Voided' (with reason)
```

#### 3. Maintain Stock Balance Integrity
```sql
-- Always verify before and after balances
START TRANSACTION;

-- Get current stock
SELECT current_stock INTO @before_stock FROM inventory_items WHERE id = 42;

-- Create movement record
INSERT INTO inventory_movements (...) VALUES (
    ...,
    @before_stock,
    @before_stock - 5,
    ...
);

-- Update stock
UPDATE inventory_items SET current_stock = @before_stock - 5 WHERE id = 42;

COMMIT;
```

#### 4. Archive Old Data Safely
```sql
-- Quarterly or annually move very old data to archive tables
INSERT INTO inventory_movements_archive
SELECT * FROM inventory_movements
WHERE movement_date < DATE_SUB(CURDATE(), INTERVAL 2 YEAR)
AND is_cancelled = FALSE;

-- Only delete after archiving confirmed
DELETE FROM inventory_movements
WHERE movement_date < DATE_SUB(CURDATE(), INTERVAL 2 YEAR)
AND is_cancelled = FALSE;
```

---

## Part 5: Handling Edge Cases

### A. Cancelled Job Orders with Used Parts

**Scenario**: Mechanic starts work, uses $500 of parts, then customer cancels

```sql
-- 1. Record the cancellation
UPDATE job_orders
SET status = 'Cancelled',
    is_deleted = FALSE,  -- Keep record
    void_reason = 'Customer cancelled mid-job'
WHERE id = 456;

-- 2. Option A: Try to return parts (if possible)
INSERT INTO inventory_movements (
    movement_number,
    inventory_item_id,
    movement_type,
    quantity,
    reason,
    reference_type,
    reference_id,
    movement_date
) VALUES (
    'MOV-RET-20260417-001',
    42,
    'RETURN',
    5,
    'Cancelled job order - parts returned to stock',
    'job_order',
    456,
    CURDATE()
);

-- 3. Option B: If parts cannot be returned, record as wastage
INSERT INTO inventory_movements (
    movement_number,
    inventory_item_id,
    movement_type,
    quantity,
    reason,
    reference_type,
    reference_id,
    movement_date
) VALUES (
    'MOV-WAST-20260417-001',
    43,
    'WASTAGE',
    3,
    'Cancelled job - damaged parts cannot be used',
    'job_order',
    456,
    CURDATE()
);

-- 4. Create credit memo or adjust sale
UPDATE sales
SET is_cancelled = TRUE,
    cancellation_reason = 'Job cancelled by customer',
    cancelled_at = CURRENT_TIMESTAMP
WHERE job_order_id = 456;
```

### B. Invoice Corrections (Overcharging/Undercharging)

**Scenario**: $50 billing error on completed sale

```sql
-- 1. Create adjustment journal entry (NOT delete original)
INSERT INTO inventory_movements (
    movement_number,
    inventory_item_id,
    movement_type,
    quantity,
    unit_price,
    reason,
    notes,
    reference_type,
    reference_id,
    movement_date
) VALUES (
    'MOV-ADJ-20260417-001',
    NULL,
    'ADJUSTMENT',
    0,
    50.00,
    'Billing correction - sales discrepancy',
    'Original invoice: INV-2026-0045, corrected amount',
    'sale',
    123,
    CURDATE()
);

-- 2. Create corrected sale document
INSERT INTO sales (
    sales_number,
    invoice_number,
    job_order_id,
    customer_id,
    subtotal,
    tax,
    discount,
    total,
    payment_status,
    notes,
    internal_notes
) VALUES (
    'SAL-COR-20260417-001',
    'INV-COR-20260417-001',
    456,
    789,
    500.00,
    50.00,
    0.00,
    550.00,
    'Pending',
    'Corrected invoice (see original INV-2026-0045)',
    'Adjustment due to pricing error: -$50'
);

-- 3. Optional: Link original for audit trail
-- Use internal_notes or create audit_log entry
```

### C. Warranty Returns & Adjustments

**Scenario**: Customer returns part under warranty 2 weeks later

```sql
-- 1. Record return
INSERT INTO inventory_movements (
    movement_number,
    inventory_item_id,
    movement_type,
    quantity,
    unit_price,
    reason,
    notes,
    reference_type,
    reference_id,
    movement_date
) VALUES (
    'MOV-WRT-20260417-001',
    42,
    'RETURN',
    1,
    35.50,
    'Warranty return - defective part',
    'Original installation: JO-2026-0456',
    'job_order',
    456,
    CURDATE()
);

-- 2. Record supplier return (when you send back to vendor)
INSERT INTO inventory_movements (
    movement_number,
    inventory_item_id,
    movement_type,
    quantity,
    unit_price,
    reason,
    notes,
    reference_type,
    purchase_order_number,
    movement_date
) VALUES (
    'MOV-SRET-20260420-001',
    42,
    'OUT',  -- Outgoing to supplier
    1,
    35.50,
    'Return to supplier for credit',
    'Warranty defect - see MOV-WRT-20260417-001',
    'purchase',
    'PO-2026-0789',
    CURDATE()
);

-- 3. Record credit from supplier when received
INSERT INTO inventory_movements (
    movement_number,
    inventory_item_id,
    movement_type,
    quantity,
    unit_price,
    reason,
    reference_type,
    purchase_order_number,
    movement_date
) VALUES (
    'MOV-SCRED-20260425-001',
    NULL,  -- No stock change
    'ADJUSTMENT',
    1,
    35.50,
    'Supplier credit for warranty return',
    'purchase',
    'PO-2026-0789',
    CURDATE()
);
```

---

## Part 6: Python/Flask Implementation Examples

### A. Model Updates (Soft Delete)

```python
# models/job_order.py
from datetime import datetime

class JobOrder(db.Model):
    __tablename__ = 'job_orders'
    
    # ... existing fields ...
    
    # Soft delete fields
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime, nullable=True)
    void_reason = db.Column(db.String(255))
    
    is_voided = db.Column(db.Boolean, default=False)
    voided_at = db.Column(db.DateTime, nullable=True)
    
    @classmethod
    def get_active_only(cls):
        """Query builder for active (non-deleted) records"""
        return cls.query.filter(
            cls.is_deleted == False,
            cls.is_voided == False
        )
    
    def soft_delete(self, reason=None):
        """Soft delete the job order"""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
        if reason:
            self.void_reason = reason
        db.session.commit()
    
    def restore(self):
        """Restore a soft-deleted job order"""
        self.is_deleted = False
        self.deleted_at = None
        db.session.commit()
    
    def void(self, reason):
        """Void a job order with reason"""
        self.is_voided = True
        self.voided_at = datetime.utcnow()
        self.void_reason = reason
        self.status = 'Voided'
        db.session.commit()
```

### B. Inventory Movement Model

```python
# models/inventory_movement.py
from datetime import datetime
from enum import Enum

class MovementType(Enum):
    IN = 'IN'
    OUT = 'OUT'
    RETURN = 'RETURN'
    ADJUSTMENT = 'ADJUSTMENT'
    WASTAGE = 'WASTAGE'

class InventoryMovement(db.Model):
    __tablename__ = 'inventory_movements'
    
    id = db.Column(db.Integer, primary_key=True)
    movement_number = db.Column(db.String(50), unique=True, nullable=False)
    
    inventory_item_id = db.Column(db.Integer, db.ForeignKey('inventory_items.id'), nullable=False)
    
    movement_type = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2))
    
    reference_type = db.Column(db.String(50))
    reference_id = db.Column(db.Integer)
    
    reason = db.Column(db.String(255))
    notes = db.Column(db.Text)
    
    status = db.Column(db.String(20), default='Completed')
    is_cancelled = db.Column(db.Boolean, default=False)
    cancellation_reason = db.Column(db.String(255))
    
    stock_before = db.Column(db.Integer)
    stock_after = db.Column(db.Integer)
    
    recorded_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    verified_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    verified_at = db.Column(db.DateTime, nullable=True)
    
    movement_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    inventory_item = db.relationship('InventoryItem', backref='movements')
    recorded_by = db.relationship('User', foreign_keys=[recorded_by_user_id])
    verified_by = db.relationship('User', foreign_keys=[verified_by_user_id])
    
    @staticmethod
    def record_usage(inventory_item_id, quantity, reference_type, reference_id, reason):
        """Record stock usage (OUT movement)"""
        item = InventoryItem.query.get(inventory_item_id)
        if not item:
            raise ValueError("Inventory item not found")
        
        if item.current_stock < quantity:
            raise ValueError(f"Insufficient stock. Available: {item.current_stock}")
        
        movement = InventoryMovement(
            movement_number=InventoryMovement.generate_movement_number('OUT'),
            inventory_item_id=inventory_item_id,
            movement_type='OUT',
            quantity=quantity,
            unit_price=item.unit_cost,
            reference_type=reference_type,
            reference_id=reference_id,
            reason=reason,
            stock_before=item.current_stock,
            stock_after=item.current_stock - quantity,
            movement_date=datetime.utcnow().date()
        )
        
        db.session.add(movement)
        item.current_stock -= quantity
        db.session.commit()
        
        return movement
    
    @staticmethod
    def record_receipt(inventory_item_id, quantity, unit_price, purchase_order_num, reason):
        """Record stock receipt (IN movement)"""
        item = InventoryItem.query.get(inventory_item_id)
        if not item:
            raise ValueError("Inventory item not found")
        
        movement = InventoryMovement(
            movement_number=InventoryMovement.generate_movement_number('IN'),
            inventory_item_id=inventory_item_id,
            movement_type='IN',
            quantity=quantity,
            unit_price=unit_price,
            reference_type='purchase',
            purchase_order_number=purchase_order_num,
            reason=reason,
            stock_before=item.current_stock,
            stock_after=item.current_stock + quantity,
            movement_date=datetime.utcnow().date()
        )
        
        db.session.add(movement)
        item.current_stock += quantity
        db.session.commit()
        
        return movement
    
    @staticmethod
    def generate_movement_number(movement_type):
        """Generate unique movement number"""
        from datetime import datetime
        prefix = movement_type[:3]  # IN, OUT, RET, ADJ, WAT
        date_str = datetime.utcnow().strftime('%Y%m%d')
        count = InventoryMovement.query.filter(
            InventoryMovement.created_at >= datetime.utcnow().replace(hour=0, minute=0, second=0)
        ).count() + 1
        return f"MOV-{prefix}-{date_str}-{count:04d}"
```

### C. Service Query Examples

```python
# models/service.py or in a service utility file

class ServiceQuery:
    @staticmethod
    def get_applicable_services_for_vehicle(vehicle_type_id):
        """Get only applicable services for a vehicle type"""
        from models import db, Service
        
        return db.session.query(Service).filter(
            Service.is_active == True,
            db.or_(
                Service.applicable_to_all == True,
                db.exists(
                    db.session.query(ServiceVehicleTypeCompatibility).filter(
                        ServiceVehicleTypeCompatibility.service_id == Service.id,
                        ServiceVehicleTypeCompatibility.vehicle_type_id == vehicle_type_id,
                        ServiceVehicleTypeCompatibility.is_applicable == True
                    )
                )
            )
        ).all()
    
    @staticmethod
    def is_service_applicable_to_vehicle(service_id, vehicle_type_id):
        """Check if specific service is applicable"""
        service = Service.query.get(service_id)
        if not service:
            return False
        
        if service.applicable_to_all:
            return True
        
        compat = ServiceVehicleTypeCompatibility.query.filter_by(
            service_id=service_id,
            vehicle_type_id=vehicle_type_id,
            is_applicable=True
        ).first()
        
        return compat is not None
```

---

## Part 7: Best Practices Summary

| Practice | Benefit | Example |
|----------|---------|---------|
| **Soft deletes with reason** | Audit trail & compliance | `is_deleted=TRUE, deleted_at=now, reason=...` |
| **Movement records** | Stock accuracy & analytics | Every `OUT`, `IN`, `RETURN` tracked |
| **Status enum fields** | Workflow control | `status IN ('Pending', 'Completed', 'Cancelled')` |
| **Snapshot fields** | Historical accuracy | Save item name/price at transaction time |
| **Balance verification** | Data integrity | `stock_before` & `stock_after` on movements |
| **Audit logs** | Compliance & debugging | Who changed what, when, why |
| **Active filters** | Query safety | Always use `.get_active_only()` or filter |
| **Archive old data** | Performance | Move records >2 years to archive quarterly |

---

## Conclusion

This design ensures:
✅ **Data Safety**: Soft deletes preserve history
✅ **Analytics-Ready**: Movement table perfect for forecasting
✅ **Compliance**: Full audit trail maintained
✅ **Scalability**: Proper indexing and relationships
✅ **Realistic**: Capstone-appropriate complexity

**Next Steps**:
1. Review this design with your team
2. Create the tables in your database
3. Update your SQLAlchemy models
4. Add soft delete methods to transactional models
5. Implement inventory movement recording
6. Add analytics queries for forecasting
