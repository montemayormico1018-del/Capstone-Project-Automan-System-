from models import db, DocumentModel
from datetime import datetime

# See models/appointment.py's _UNSET for why: None is a valid "no sale yet"
# result and can't also mean "not looked up".
_UNSET = object()


def _as_date(value):
    """Firestore has no date-only type, so estimated_completion (copied from
    Appointment.preferred_date) is stored as an ISO string. Coerce back to a
    real date on read, matching models/appointment.py's _as_date."""
    if isinstance(value, str):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            return value
    return value


class JobOrder(DocumentModel):
    """Job Order model for work orders created from approved appointments."""
    __collection__ = 'job_orders'

    def __init__(self, **kwargs):
        self.appointment_id = kwargs.pop('appointment_id', None)
        self.vehicle_id = kwargs.pop('vehicle_id', None)
        self.user_id = kwargs.pop('user_id', None)
        self.job_order_number = kwargs.pop('job_order_number', None)
        self.status = kwargs.pop('status', 'Pending')
        self.plate_number = kwargs.pop('plate_number', None)
        self.mileage = kwargs.pop('mileage', None)
        self.diagnosis = kwargs.pop('diagnosis', None)
        self.work_description = kwargs.pop('work_description', None)
        self.recommendations = kwargs.pop('recommendations', None)
        self.completed_services = kwargs.pop('completed_services', None) or {}
        self.service_ids = kwargs.pop('service_ids', None)
        # Not populated at creation (mirrors service_ids, which is likewise
        # never copied from the appointment) — job orders derive their
        # package list from self.appointment.packages at read time instead.
        self.package_ids = kwargs.pop('package_ids', None)
        self.completed_package_items = kwargs.pop('completed_package_items', None) or {}
        self.labor_cost = kwargs.pop('labor_cost', 0.0)
        # Manually priced irregular work is kept in the job-order document so
        # it is preserved in Cloud Firestore with the historical quotation.
        self.additional_items = kwargs.pop('additional_items', None) or []
        self.parts_cost = kwargs.pop('parts_cost', 0.0)
        self.total_cost = kwargs.pop('total_cost', 0.0)
        self.priority = kwargs.pop('priority', 'Normal')
        self.estimated_completion = _as_date(kwargs.pop('estimated_completion', None))
        self.notes = kwargs.pop('notes', None)
        self.mechanic_notes = kwargs.pop('mechanic_notes', None)
        # Soft delete fields
        self.is_deleted = kwargs.pop('is_deleted', False)
        self.deleted_at = kwargs.pop('deleted_at', None)
        self.deleted_reason = kwargs.pop('deleted_reason', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.started_at = kwargs.pop('started_at', None)
        self.completed_at = kwargs.pop('completed_at', None)
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        # In-memory
        self._items = kwargs.pop('items', None)
        self._sale = kwargs.pop('sale', _UNSET)
        self._appointment = kwargs.pop('appointment', _UNSET)
        self._vehicle = kwargs.pop('vehicle', _UNSET)
        self._user = kwargs.pop('user', _UNSET)
        super().__init__(**kwargs)

    def calculate_totals(self):
        self.parts_cost = sum(item.quantity_used * item.unit_price for item in self.items)
        additional_total = sum(
            (float(item.get('quantity', 0)) * float(item.get('unit_price', 0)))
            for item in self.additional_items if isinstance(item, dict)
        )
        self.total_cost = (self.labor_cost or 0.0) + self.parts_cost + additional_total

    def get_services_progress(self):
        svcs = self.appointment.services if self.appointment else []
        total = len(svcs)
        # Some legacy records have completed_services stored as something
        # other than a map (seen in production: a bare string) — treat
        # anything non-dict as "nothing completed yet" rather than crashing.
        completed_svcs = self.completed_services
        if not isinstance(completed_svcs, dict):
            completed_svcs = {}
        if not completed_svcs:
            return 0, total
        completed = 0
        for s in svcs:
            sid = str(s.id)
            if completed_svcs.get(sid, False) or completed_svcs.get(s.id, False):
                completed += 1
        return completed, total

    def get_package_progress(self):
        pkgs = self.appointment.packages if self.appointment else []
        all_items = []
        for pkg in pkgs:
            all_items.extend(pkg.applicable_items(self.vehicle))
        total = len(all_items)
        completed_map = self.completed_package_items
        if not isinstance(completed_map, dict):
            completed_map = {}
        if not completed_map:
            return 0, total
        completed = 0
        for item in all_items:
            iid = str(item.id)
            if completed_map.get(iid, False) or completed_map.get(item.id, False):
                completed += 1
        return completed, total

    @property
    def appointment(self):
        from models.appointment import Appointment
        if self._appointment is not _UNSET:
            return self._appointment
        self._appointment = Appointment.get(self.appointment_id) if self.appointment_id else None
        return self._appointment

    @property
    def vehicle(self):
        from models.vehicle import Vehicle
        if self._vehicle is not _UNSET:
            return self._vehicle
        self._vehicle = Vehicle.get(self.vehicle_id) if self.vehicle_id else None
        return self._vehicle

    @property
    def user(self):
        from models.user import User
        if self._user is not _UNSET:
            return self._user
        self._user = User.get(self.user_id) if self.user_id else None
        return self._user

    @property
    def items(self):
        from models.job_order_item import JobOrderItem
        if self._items is not None:
            return self._items
        self._items = JobOrderItem.query.filter_by(job_order_id=self.id).all()
        return self._items

    def to_dict(self, include_appointment=True, include_sale=True):
        completed, total = self.get_services_progress()
        completed_pkg, total_pkg = self.get_package_progress()
        result = {
            'id': self.id,
            'appointment_id': self.appointment_id,
            'vehicle_id': self.vehicle_id,
            'user_id': self.user_id,
            'job_order_number': self.job_order_number,
            'status': self.status,
            'priority': self.priority,
            'plate_number': self.plate_number,
            'mileage': self.mileage,
            'diagnosis': self.diagnosis,
            'work_description': self.work_description,
            'recommendations': self.recommendations,
            'completed_services': self.completed_services if isinstance(self.completed_services, dict) else {},
            'services_progress': {'completed': completed, 'total': total},
            'completed_package_items': self.completed_package_items if isinstance(self.completed_package_items, dict) else {},
            'package_progress': {'completed': completed_pkg, 'total': total_pkg},
            'labor_cost': self.labor_cost,
            'additional_items': self.additional_items,
            'parts_cost': self.parts_cost,
            'total_cost': self.total_cost,
            'notes': self.notes,
            'mechanic_notes': self.mechanic_notes,
            'estimated_completion': self.estimated_completion.isoformat() if hasattr(self.estimated_completion, 'isoformat') else self.estimated_completion,
            'created_at': self.created_at.isoformat() if hasattr(self.created_at, 'isoformat') else self.created_at,
            'started_at': self.started_at.isoformat() if hasattr(self.started_at, 'isoformat') else self.started_at,
            'completed_at': self.completed_at.isoformat() if hasattr(self.completed_at, 'isoformat') else self.completed_at,
            'vehicle': self.vehicle.to_dict() if self.vehicle else None,
            'user': self.user.to_dict() if self.user else None,
            'items': [item.to_dict() for item in self.items]
        }
        if include_appointment and self.appointment:
            result['appointment'] = self.appointment.to_dict()
        if include_sale and self.sale:
            result['sale'] = self.sale.to_dict()
        return result

    @property
    def sale(self):
        if self._sale is _UNSET:
            from models.sale import Sale
            self._sale = Sale.find_by(job_order_id=self.id)
        return self._sale

    @classmethod
    def prefetch(cls, job_orders):
        """Batch-load vehicle/user/items/sale/appointment (and the
        appointment's own customer/vehicle/services/packages) for a list of
        job orders, in place of one round trip per relation per job order.
        Mirrors Appointment.prefetch() — see that docstring for why this
        matters; /api/job-orders showed the same ~4s latency pattern since
        to_dict() also serializes each job order's linked appointment.
        """
        from models.vehicle import Vehicle
        from models.user import User
        from models.job_order_item import JobOrderItem
        from models.sale import Sale, SaleItem
        from models.appointment import Appointment
        from concurrent.futures import ThreadPoolExecutor

        job_orders = [j for j in job_orders if j is not None]
        if not job_orders:
            return job_orders

        def _appointments():
            appt_ids = [j.appointment_id for j in job_orders if j.appointment_id]
            appts = Appointment.get_many(appt_ids)
            Appointment.prefetch(appts.values())  # its own internal fan-out
            return appts

        def _items_and_sales():
            items_by_jo = {}
            sales_by_jo = {}
            ids = [j.id for j in job_orders if j.id]
            for i in range(0, len(ids), 30):  # Firestore 'in' filter cap
                chunk = ids[i:i + 30]
                for item in JobOrderItem.query.filter(JobOrderItem.job_order_id.in_(chunk)).all():
                    items_by_jo.setdefault(item.job_order_id, []).append(item)
                for sale in Sale.query.filter(Sale.job_order_id.in_(chunk)).all():
                    sales_by_jo[sale.job_order_id] = sale

            # Each sale's own to_dict() reads self.items (SaleItem) and
            # self.job_order — without batching the former and pointing the
            # latter back at the job order object we already built here, one
            # job order with a sale turned into: a SaleItem query, PLUS a
            # completely fresh JobOrder.get() re-fetch of the very job order
            # already in hand (uncached, so its own vehicle/user/items were
            # each queried again too). Measured at ~1s per such job order.
            sale_ids = [s.id for s in sales_by_jo.values() if s.id]
            items_by_sale = {}
            for i in range(0, len(sale_ids), 30):
                chunk = sale_ids[i:i + 30]
                for item in SaleItem.query.filter(SaleItem.sale_id.in_(chunk)).all():
                    items_by_sale.setdefault(item.sale_id, []).append(item)
            for sale in sales_by_jo.values():
                sale._items = items_by_sale.get(sale.id, [])

            # JobOrderItem.to_dict() reads self.inventory_item — batch it so
            # a job order with N parts isn't N more InventoryItem.get()s.
            from models.inventory import InventoryItem
            all_items = [item for items in items_by_jo.values() for item in items]
            inv_items = InventoryItem.get_many(item.inventory_item_id for item in all_items)
            for item in all_items:
                item._inventory_item = inv_items.get(str(item.inventory_item_id))

            return items_by_jo, sales_by_jo

        # Four independent read paths — run them concurrently rather than
        # waiting on each round trip (or chunked batch of round trips) in
        # turn. See Appointment.prefetch's docstring for the same reasoning.
        with ThreadPoolExecutor(max_workers=4) as pool:
            f_vehicles = pool.submit(Vehicle.get_many, [j.vehicle_id for j in job_orders])
            f_users = pool.submit(User.get_many, [j.user_id for j in job_orders])
            f_appointments = pool.submit(_appointments)
            f_items_sales = pool.submit(_items_and_sales)

            vehicles = f_vehicles.result()
            users = f_users.result()
            appointments = f_appointments.result()
            items_by_jo, sales_by_jo = f_items_sales.result()

        # Sale.to_dict() reads vehicle.customer.name — batch it here too so
        # that doesn't cost one more Customer.get() per (unique) vehicle.
        from models.customer import Customer
        customers_by_vehicle = Customer.get_many(v.customer_id for v in vehicles.values())
        for v in vehicles.values():
            v._customer = customers_by_vehicle.get(v.customer_id)

        for j in job_orders:
            j._vehicle = vehicles.get(j.vehicle_id)
            j._user = users.get(j.user_id)
            j._items = items_by_jo.get(j.id, [])
            sale = sales_by_jo.get(j.id)
            j._sale = sale  # None is now a final answer, not "unloaded"
            if sale is not None:
                sale._job_order = j  # this job order IS the sale's job order — skip its own re-fetch
            appt = appointments.get(j.appointment_id)
            if appt is not None:
                appt._job_order = j  # this job order IS the appointment's job order
            j._appointment = appt
        return job_orders
