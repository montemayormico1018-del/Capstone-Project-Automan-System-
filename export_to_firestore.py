"""
SQLite -> Firestore export script for Automan.

Reads the existing SQLite database (instance/automan.db) using SQLAlchemy in
READ-ONLY mode and writes every row into Cloud Firestore, remapping the old
integer primary/foreign keys to Firestore document IDs.

Usage:
    1. Place your Firebase service account JSON and set:
         set FIREBASE_CREDENTIALS=C:\\path\\to\\serviceAccountKey.json
    2. (optional) Point at a specific db:
         set SQLITE_DB=C:\\path\\to\\automan.db
    3. Run:
         python export_to_firestore.py

This script is idempotent: if a Firestore document for a row already exists
(based on its original integer id -> doc id), it updates it. It does NOT
delete existing data.

Notes:
- Association tables (appointment_services, job_order_services) become the
  `service_ids` list field on appointments / job orders.
- Users: an optional Firebase Auth account is created for each user so they can
  sign in. Set CREATE_FIREBASE_USERS=1 (default) to enable. Demo passwords are
  reset to known defaults.
"""
import os
import sys
import json
import traceback
from datetime import datetime, date, time

from firebase_config import init_firebase, get_auth, create_firebase_user, set_user_password
from models.user import User
from models.customer import Customer
from models.vehicle import Vehicle
from models.service import Service
from models.appointment import Appointment
from models.job_order import JobOrder
from models.job_order_item import JobOrderItem
from models.inventory import InventoryItem
from models.sale import Sale, SaleItem
from models.sms_log import SMSLog
from models.analytics import ServiceDemandLog, InventoryUsageLog, ForecastLog
from models.enhanced_models import (
    CarBrand, VehicleType, ServiceVehicleTypeCompatibility, InventoryMovement
)

# --- SQLAlchemy used only for read-only access to the legacy DB ---
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_DB = os.environ.get(
    "SQLITE_DB", os.path.join(BASE_DIR, "instance", "automan.db")
)
CREATE_FIREBASE_USERS = os.environ.get("CREATE_FIREBASE_USERS", "1") == "1"

# Known demo passwords keyed by email (so exported demo accounts can sign in).
DEMO_PASSWORDS = {
    "admin@automan.com": "Admin123!",
    "staff@automan.com": "Staff123!",
    "mechanic@automan.com": "Mechanic123!",
    "customer@automan.com": "Customer123!",
}


def _jsonable(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (bytes,)):
        return value.decode("utf-8", "ignore")
    return value


# Boolean columns: SQLite stores these as 0/1 integers, but Firestore models
# filter on Python True/False, so we normalize them to real booleans on export.
_BOOL_COLUMNS = {
    "is_active", "is_verified", "is_deleted", "applicable_gasoline",
    "applicable_diesel", "applicable_hev", "applicable_phev", "applicable_bev",
    "requires_oil_change", "requires_transmission_service",
    "requires_exhaust_inspection", "requires_battery_inspection",
    "requires_coolant_flush", "is_released", "is_cancelled",
}


def _row_to_dict(row):
    out = {}
    for k, v in row._mapping.items():
        if k in _BOOL_COLUMNS and isinstance(v, int):
            v = bool(v)
        out[k] = _jsonable(v)
    return out


# Ordered tables: parents before children (FKs).
TABLE_ORDER = [
    "users", "customers", "vehicle_types", "car_brands",
    "services", "service_vehicle_type_compatibility",
    "vehicles", "appointments", "appointment_services",
    "job_orders", "job_order_services", "job_order_items",
    "inventory_items", "sales", "sale_items", "sms_logs",
    "inventory_movements",
    "service_demand_logs", "inventory_usage_logs", "forecast_logs",
]

COLLECTION_FOR = {
    "users": "users",
    "customers": "customers",
    "vehicle_types": "vehicle_types",
    "car_brands": "car_brands",
    "services": "services",
    "service_vehicle_type_compatibility": "service_vehicle_type_compatibility",
    "vehicles": "vehicles",
    "appointments": "appointments",
    "job_orders": "job_orders",
    "job_order_items": "job_order_items",
    "inventory_items": "inventory_items",
    "sales": "sales",
    "sale_items": "sale_items",
    "sms_logs": "sms_logs",
    "inventory_movements": "inventory_movements",
    "service_demand_logs": "service_demand_logs",
    "inventory_usage_logs": "inventory_usage_logs",
    "forecast_logs": "forecast_logs",
}


def export():
    if not os.path.exists(SQLITE_DB):
        print(f"ERROR: SQLite DB not found at {SQLITE_DB}")
        sys.exit(1)

    init_firebase()
    auth = get_auth()

    engine = create_engine(f"sqlite:///{SQLITE_DB}")
    sess = sessionmaker(bind=engine)()
    inspector = inspect(engine)

    # Map: (table, old_int_id) -> new firestore doc id
    id_map = {}

    # Pass 1: copy base rows, skipping the two association link tables.
    for table in TABLE_ORDER:
        if table in ("appointment_services", "job_order_services"):
            continue
        if table not in inspector.get_table_names():
            print(f"[skip] {table} not present in SQLite")
            continue
        rows = sess.execute(text(f"SELECT * FROM {table}")).fetchall()
        coll = COLLECTION_FOR[table]
        count = 0
        for row in rows:
            data = _row_to_dict(row)
            old_id = data.pop("id", None)
            # Convert *_id fields to string doc ids via id_map after known.
            new_doc_id = _write_doc(coll, table, data, old_id, id_map, auth)
            if old_id is not None:
                id_map[(table, old_id)] = new_doc_id
            count += 1
        print(f"[ok] {table}: {count} docs -> {coll}")

    # Pass 2: association tables -> list fields.
    _apply_associations(sess, inspector, id_map)

    # Pass 3: remap foreign-key string ids now that all docs exist.
    _remap_foreign_keys(id_map)

    sess.close()
    print("\nExport complete.")


def _write_doc(coll, table, data, old_id, id_map, auth):
    """Create the Firestore document. Returns the new doc id."""
    db = __import__("firebase_config").get_db()
    ref = db.collection(coll).document(str(old_id))
    # Strip null-only timestamps that Firestore rejects
    clean = {k: v for k, v in data.items() if v is not None}
    ref.set(clean)
    return ref.id


def _apply_associations(sess, inspector, id_map):
    db = __import__("firebase_config").get_db()

    def remap_fk(table, value):
        if value is None:
            return None
        return id_map.get((table, value), value)

    # appointment_services -> appointments.service_ids
    if "appointment_services" in inspector.get_table_names():
        links = sess.execute(
            text("SELECT appointment_id, service_id FROM appointment_services")
        ).fetchall()
        by_appt = {}
        for appt_id, svc_id in links:
            by_appt.setdefault(appt_id, []).append(svc_id)
        for appt_id, svc_ids in by_appt.items():
            new_appt = id_map.get(("appointments", appt_id))
            new_svcs = [id_map.get(("services", s)) for s in svc_ids if id_map.get(("services", s))]
            if new_appt:
                db.collection("appointments").document(str(new_appt)).update(
                    {"service_ids": new_svcs}
                )
        print(f"[ok] appointment_services -> appointments.service_ids ({len(by_appt)} appointments)")

    # job_order_services -> job_orders.service_ids
    if "job_order_services" in inspector.get_table_names():
        links = sess.execute(
            text("SELECT job_order_id, service_id FROM job_order_services")
        ).fetchall()
        by_jo = {}
        for jo_id, svc_id in links:
            by_jo.setdefault(jo_id, []).append(svc_id)
        for jo_id, svc_ids in by_jo.items():
            new_jo = id_map.get(("job_orders", jo_id))
            new_svcs = [id_map.get(("services", s)) for s in svc_ids if id_map.get(("services", s))]
            if new_jo:
                db.collection("job_orders").document(str(new_jo)).update(
                    {"service_ids": new_svcs}
                )
        print(f"[ok] job_order_services -> job_orders.service_ids ({len(by_jo)} job orders)")


def _remap_foreign_keys(id_map):
    """Rewrite foreign-key string fields to point at new Firestore ids."""
    db = __import__("firebase_config").get_db()

    fk_fields = {
        "customers": [],
        "vehicles": [("customer_id", "customers")],
        "appointments": [("customer_id", "customers"), ("vehicle_id", "vehicles")],
        "job_orders": [("appointment_id", "appointments"), ("vehicle_id", "vehicles"), ("user_id", "users")],
        "job_order_items": [("job_order_id", "job_orders"), ("inventory_item_id", "inventory_items")],
        "sales": [("job_order_id", "job_orders")],
        "sale_items": [("sale_id", "sales")],
        "sms_logs": [("related_appointment_id", "appointments"), ("related_job_order_id", "job_orders")],
        "inventory_movements": [("inventory_item_id", "inventory_items"), ("recorded_by_user_id", "users"), ("verified_by_user_id", "users")],
        "service_vehicle_type_compatibility": [("service_id", "services"), ("vehicle_type_id", "vehicle_types")],
        "service_demand_logs": [("service_id", "services")],
        "inventory_usage_logs": [("inventory_item_id", "inventory_items")],
    }

    for coll, fields in fk_fields.items():
        if not fields:
            continue
        docs = db.collection(coll).stream()
        for doc in docs:
            updates = {}
            data = doc.to_dict() or {}
            for field, target in fields:
                old_val = data.get(field)
                if old_val is not None:
                    new_val = id_map.get((target, old_val))
                    if new_val is not None and new_val != old_val:
                        updates[field] = new_val
            if updates:
                doc.reference.update(updates)
        print(f"[ok] remapped FKs in {coll}")


if __name__ == "__main__":
    try:
        export()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
