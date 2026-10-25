from models import db, DocumentModel
from datetime import datetime

# See models/appointment.py's _UNSET for why: a customer_id can point at a
# since-deleted document, and None can't double as both "not looked up yet"
# and "looked up, genuinely doesn't exist" without re-querying forever.
_UNSET = object()


class Vehicle(DocumentModel):
    """Vehicle model for customer vehicles."""
    __collection__ = 'vehicles'

    def __init__(self, **kwargs):
        self.customer_id = kwargs.pop('customer_id', None)
        self.make = kwargs.pop('make', None)
        self.model = kwargs.pop('model', None)
        self.year = kwargs.pop('year', None)
        self.vehicle_type = kwargs.pop('vehicle_type', 'Gasoline')
        # size_class drives per-vehicle pricing tiers on packages/services
        # (e.g. a Vios vs a Fortuner get different prices for the same
        # package). 'small' | 'midsize' | 'large'.
        self.size_class = kwargs.pop('size_class', None)
        self.license_plate = kwargs.pop('license_plate', None)
        # identifier_type: 'plate_number' | 'conduction_sticker' (brand-new PH
        # vehicles carry a temporary sticker before a real plate is issued).
        self.identifier_type = kwargs.pop('identifier_type', 'plate_number')
        self.plate_or_sticker_no = kwargs.pop('plate_or_sticker_no', None)
        if not self.plate_or_sticker_no and self.license_plate:
            # Legacy records only ever populated license_plate; backfill lazily
            # on read so old vehicles display correctly with no migration.
            self.plate_or_sticker_no = self.license_plate
        self.vin = kwargs.pop('vin', None)
        self.color = kwargs.pop('color', None)
        self.mileage = kwargs.pop('mileage', None)
        self.notes = kwargs.pop('notes', None)
        self.image_url = kwargs.pop('image_url', None)
        # Soft delete fields
        self.is_deleted = kwargs.pop('is_deleted', False)
        self.deleted_at = kwargs.pop('deleted_at', None)
        self.deleted_reason = kwargs.pop('deleted_reason', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        self._customer = kwargs.pop('customer', _UNSET)
        super().__init__(**kwargs)

    @property
    def customer(self):
        from models.customer import Customer
        if self._customer is not _UNSET:
            return self._customer
        self._customer = Customer.get(self.customer_id) if self.customer_id else None
        return self._customer

    def to_dict(self):
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'make': self.make,
            'model': self.model,
            'year': self.year,
            'vehicle_type': self.vehicle_type,
            'size_class': self.size_class,
            'license_plate': self.license_plate,
            'identifier_type': self.identifier_type,
            'plate_or_sticker_no': self.plate_or_sticker_no,
            'vin': self.vin,
            'color': self.color,
            'mileage': self.mileage,
            'notes': self.notes,
            'image_url': self.image_url
        }
