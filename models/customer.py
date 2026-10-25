from models import db, DocumentModel
from datetime import datetime


class Customer(DocumentModel):
    """Customer model for vehicle owners."""
    __collection__ = 'customers'

    def __init__(self, **kwargs):
        self.name = kwargs.pop('name', None)
        self.email = kwargs.pop('email', None)
        self.phone = kwargs.pop('phone', None)
        self.address = kwargs.pop('address', None)
        # Soft delete fields (Transactional Data - NO HARD DELETE)
        self.is_deleted = kwargs.pop('is_deleted', False)
        self.deleted_at = kwargs.pop('deleted_at', None)
        self.deleted_reason = kwargs.pop('deleted_reason', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        # Related vehicles loaded on demand
        self._vehicles = kwargs.pop('vehicles', None)
        self._appointments = kwargs.pop('appointments', None)
        super().__init__(**kwargs)

    @property
    def vehicles(self):
        from models.vehicle import Vehicle
        if self._vehicles is not None:
            return self._vehicles
        self._vehicles = Vehicle.query.filter_by(customer_id=self.id).all()
        return self._vehicles

    @property
    def appointments(self):
        from models.appointment import Appointment
        if self._appointments is not None:
            return self._appointments
        self._appointments = Appointment.query.filter_by(customer_id=self.id).all()
        return self._appointments

    @classmethod
    def prefetch(cls, customers):
        """Batch-load vehicles for a list of customers.

        Without this, Customer.to_dict() makes one Vehicle query per
        customer (via the `vehicles` property) — the customers list page
        was one round trip for the list plus one more per customer just to
        render their vehicle counts.
        """
        from models.vehicle import Vehicle

        customers = [c for c in customers if c is not None]
        if not customers:
            return customers

        vehicles_by_customer = {}
        ids = [c.id for c in customers if c.id]
        for i in range(0, len(ids), 30):  # Firestore 'in' filter cap
            chunk = ids[i:i + 30]
            for v in Vehicle.query.filter(Vehicle.customer_id.in_(chunk)).all():
                vehicles_by_customer.setdefault(v.customer_id, []).append(v)

        for c in customers:
            c._vehicles = vehicles_by_customer.get(c.id, [])
        return customers

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'address': self.address,
            'vehicles': [v.to_dict() for v in self.vehicles],
            'created_at': self.created_at.isoformat() if hasattr(self.created_at, 'isoformat') else self.created_at
        }
