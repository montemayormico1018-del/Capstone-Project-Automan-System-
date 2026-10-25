from models import db, DocumentModel
from datetime import datetime

# Distinguishes "never looked up" from "looked up, no job order exists" so
# Appointment.prefetch() can cache the latter without job_order re-querying
# on every access (None can't do this since it's also the "not loaded" value).
_UNSET = object()


def _as_date(value):
    """Firestore has no date-only type, so preferred_date is stored as an ISO
    string ('YYYY-MM-DD'). Coerce it back to a real date on read so callers
    can keep using .isoformat()/comparisons/etc like before."""
    if isinstance(value, str):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            return value
    return value


def _as_time(value):
    """Same as _as_date, but for the time-only preferred_time field
    ('HH:MM:SS')."""
    if isinstance(value, str):
        try:
            return datetime.strptime(value, '%H:%M:%S').time()
        except ValueError:
            return value
    return value


class Appointment(DocumentModel):
    """Appointment model for customer service requests."""
    __collection__ = 'appointments'

    def __init__(self, **kwargs):
        self.appointment_number = kwargs.pop('appointment_number', None)
        self.customer_id = kwargs.pop('customer_id', None)
        self.vehicle_id = kwargs.pop('vehicle_id', None)
        self.plate_number = kwargs.pop('plate_number', None)
        self.preferred_date = _as_date(kwargs.pop('preferred_date', None))
        self.preferred_time = _as_time(kwargs.pop('preferred_time', None))
        self.customer_notes = kwargs.pop('customer_notes', None)
        self.notes = kwargs.pop('notes', None)
        self.rejection_reason = kwargs.pop('rejection_reason', None)
        self.rejection_message = kwargs.pop('rejection_message', None)
        self.status = kwargs.pop('status', 'Pending')
        self.service_estimate = kwargs.pop('service_estimate', 0.0)
        self.min_estimate = kwargs.pop('min_estimate', 0.0)
        self.max_estimate = kwargs.pop('max_estimate', 0.0)
        # Audit trail: who set/edited the price range and when, so a staff
        # override is traceable rather than silently overwriting the field.
        self.price_history = kwargs.pop('price_history', None) or []
        # Service associations (replaces appointment_services link table)
        self.service_ids = kwargs.pop('service_ids', None)
        self.package_ids = kwargs.pop('package_ids', None)
        # Soft delete fields
        self.is_deleted = kwargs.pop('is_deleted', False)
        self.deleted_at = kwargs.pop('deleted_at', None)
        self.deleted_reason = kwargs.pop('deleted_reason', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        # In-memory related objects for serialization
        self._services = kwargs.pop('services', None)
        self._packages = kwargs.pop('packages', None)
        # _UNSET (not None) distinguishes "never looked up" from "looked up,
        # genuinely doesn't exist" — a customer/vehicle id can point at a
        # since-deleted document, and without this every subsequent access
        # (e.g. once per to_dict() call) would silently redo the same
        # failed Firestore lookup instead of caching the "not found" answer.
        self._customer = kwargs.pop('customer', _UNSET)
        self._vehicle = kwargs.pop('vehicle', _UNSET)
        self._job_order = kwargs.pop('job_order', _UNSET)
        super().__init__(**kwargs)

    @property
    def services(self):
        from models.service import Service
        if self._services is not None:
            return self._services
        if not self.service_ids:
            self._services = []
        else:
            self._services = [s for s in (Service.get(sid) for sid in self.service_ids) if s]
        return self._services

    @property
    def packages(self):
        from models.service_package import ServicePackage
        if self._packages is not None:
            return self._packages
        if not self.package_ids:
            self._packages = []
        else:
            self._packages = [p for p in (ServicePackage.get(pid) for pid in self.package_ids) if p]
        return self._packages

    @property
    def customer(self):
        from models.customer import Customer
        if self._customer is not _UNSET:
            return self._customer
        self._customer = Customer.get(self.customer_id) if self.customer_id else None
        return self._customer

    @property
    def vehicle(self):
        from models.vehicle import Vehicle
        if self._vehicle is not _UNSET:
            return self._vehicle
        self._vehicle = Vehicle.get(self.vehicle_id) if self.vehicle_id else None
        return self._vehicle

    @property
    def job_order(self):
        if self._job_order is _UNSET:
            self._job_order = JobOrder.find_by(appointment_id=self.id) if self.id else None
        return self._job_order

    @property
    def has_job_order(self):
        return self.job_order is not None

    def to_dict(self):
        services = []
        seen = set()
        for service in self.services:
            if service.id not in seen:
                seen.add(service.id)
                services.append(service)

        packages = []
        seen_pkg = set()
        for package in self.packages:
            if package.id not in seen_pkg:
                seen_pkg.add(package.id)
                packages.append(package)

        from utils.pricing import compute_suggested_estimate, vehicle_size_class

        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'vehicle_id': self.vehicle_id,
            'appointment_number': self.appointment_number,
            'plate_number': self.plate_number,
            'preferred_date': self.preferred_date.isoformat() if hasattr(self.preferred_date, 'isoformat') else self.preferred_date,
            'preferred_time': self.preferred_time.strftime('%H:%M') if hasattr(self.preferred_time, 'strftime') else self.preferred_time,
            'customer_notes': self.customer_notes,
            'notes': self.notes,
            'rejection_reason': self.rejection_reason,
            'rejection_message': self.rejection_message,
            'status': self.status,
            'service_estimate': self.service_estimate,
            'min_estimate': self.min_estimate,
            'max_estimate': self.max_estimate,
            'price_history': self.price_history,
            'created_at': self.created_at.isoformat() if hasattr(self.created_at, 'isoformat') else self.created_at,
            'updated_at': self.updated_at.isoformat() if hasattr(self.updated_at, 'isoformat') else self.updated_at,
            'has_job_order': self.has_job_order,
            'customer': self.customer.to_dict() if self.customer else None,
            'vehicle': self.vehicle.to_dict() if self.vehicle else None,
            'services': [s.to_dict() for s in services],
            'package_ids': self.package_ids,
            'packages': [p.to_dict(vehicle=self.vehicle) for p in packages],
            'suggested_estimate': compute_suggested_estimate(self),
            'vehicle_size_class': vehicle_size_class(self.vehicle)
        }

    @classmethod
    def prefetch(cls, appointments):
        """Batch-load customer/vehicle/services/packages/job_order for a list
        of appointments and populate their cache slots in place.

        Without this, calling .to_dict() on each appointment in a list makes
        one Firestore round trip per appointment for its customer, one for
        its vehicle, one per service, one per package, plus a full query to
        check for a linked job order — turning a list of a dozen appointments
        into 50+ sequential network calls (this was measured at ~4s on
        /api/appointments in production with only a handful of records).
        Call this once on the full list before serializing.
        """
        from models.customer import Customer
        from models.vehicle import Vehicle
        from models.service import Service
        from models.service_package import ServicePackage
        from concurrent.futures import ThreadPoolExecutor

        appointments = [a for a in appointments if a is not None]
        if not appointments:
            return appointments

        def _job_orders_by_appt():
            result = {}
            ids = [a.id for a in appointments if a.id]
            for i in range(0, len(ids), 30):  # Firestore 'in' filter cap
                chunk = ids[i:i + 30]
                for jo in JobOrder.query.filter(JobOrder.appointment_id.in_(chunk)).all():
                    result[jo.appointment_id] = jo
            return result

        def _packages():
            pkgs = ServicePackage.get_many(pid for a in appointments for pid in (a.package_ids or []))
            # Package.to_dict() reads each package's checklist (.items) — a
            # to_dict()-shaped serialization (appointment -> packages ->
            # applicable_items) needs those batched too, or get_many() alone
            # just moves the per-package PackageItem query later, not away.
            ServicePackage.prefetch(pkgs.values())
            return pkgs

        # These five reads don't depend on each other — each is its own
        # round trip (or chunked batch of round trips) to Firestore, so
        # running them concurrently instead of one-after-another turns N
        # sequential network waits into ~1 (the slowest of the five).
        with ThreadPoolExecutor(max_workers=5) as pool:
            f_customers = pool.submit(Customer.get_many, [a.customer_id for a in appointments])
            f_vehicles = pool.submit(Vehicle.get_many, [a.vehicle_id for a in appointments])
            f_services = pool.submit(Service.get_many, [sid for a in appointments for sid in (a.service_ids or [])])
            f_packages = pool.submit(_packages)
            f_job_orders = pool.submit(_job_orders_by_appt)

            customers = f_customers.result()
            vehicles = f_vehicles.result()
            services = f_services.result()
            packages = f_packages.result()
            job_orders_by_appt = f_job_orders.result()

        for a in appointments:
            a._customer = customers.get(a.customer_id)
            a._vehicle = vehicles.get(a.vehicle_id)
            a._services = [services[sid] for sid in (a.service_ids or []) if sid in services]
            a._packages = [packages[pid] for pid in (a.package_ids or []) if pid in packages]
            a._job_order = job_orders_by_appt.get(a.id)  # None here is now a final answer, not "unloaded"
        return appointments


from models.job_order import JobOrder  # noqa: E402  (circular import safe)
