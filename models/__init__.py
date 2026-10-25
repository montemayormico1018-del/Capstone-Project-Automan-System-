from firestore_db import db, DocumentModel

# Original models (Firestore-backed)
from models.user import User
from models.customer import Customer
from models.vehicle import Vehicle
from models.service import Service
from models.service_package import ServicePackage
from models.package_item import PackageItem
from models.appointment import Appointment
from models.job_order import JobOrder
from models.job_order_item import JobOrderItem
from models.inventory import InventoryItem
from models.sale import Sale, SaleItem
from models.sms_log import SMSLog
from models.analytics import ServiceDemandLog, InventoryUsageLog, ForecastLog
from models.audit_log import AuditLog
from models.loyalty import (
    LoyaltyAccount,
    LoyaltyTransaction,
    Reward,
    RewardRedemption,
)

# Enhanced models (Firestore-backed)
from models.enhanced_models import (
    CarBrand,
    VehicleType,
    ServiceVehicleTypeCompatibility,
    InventoryMovement
)
