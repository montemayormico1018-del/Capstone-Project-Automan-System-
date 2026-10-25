from models import db, DocumentModel
from datetime import datetime


class Service(DocumentModel):
    """Service model for available car services."""
    __collection__ = 'services'

    def __init__(self, **kwargs):
        self.name = kwargs.pop('name', None)
        self.description = kwargs.pop('description', None)
        self.category = kwargs.pop('category', None)
        self.icon = kwargs.pop('icon', None)
        self.price = kwargs.pop('price', 0.0)
        # Optional per-vehicle-size-class overrides (same pattern as
        # ServicePackage.price_small/price_large) — price doubles as the
        # 'midsize' price, small/large fall back to it when unset.
        self.price_small = kwargs.pop('price_small', None)
        self.price_large = kwargs.pop('price_large', None)
        self.duration_minutes = kwargs.pop('duration_minutes', None)
        self.parent_id = kwargs.pop('parent_id', None)
        self.is_active = kwargs.pop('is_active', True)
        # Vehicle applicability flags
        self.applicable_gasoline = kwargs.pop('applicable_gasoline', True)
        self.applicable_diesel = kwargs.pop('applicable_diesel', True)
        self.applicable_hev = kwargs.pop('applicable_hev', True)
        self.applicable_phev = kwargs.pop('applicable_phev', True)
        self.applicable_bev = kwargs.pop('applicable_bev', True)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        # In-memory only (not persisted as a field here)
        self.sub_services = kwargs.pop('sub_services', None)
        super().__init__(**kwargs)

    def price_for(self, size_class):
        """Price for a given vehicle size class ('small'/'midsize'/'large'),
        falling back to price when no override is set for that class."""
        if size_class == 'small' and self.price_small is not None:
            return self.price_small
        if size_class == 'large' and self.price_large is not None:
            return self.price_large
        return self.price

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'category': self.category,
            'icon': self.icon,
            'price': self.price,
            'price_small': self.price_small,
            'price_large': self.price_large,
            'duration_minutes': self.duration_minutes,
            'parent_id': self.parent_id,
            'is_active': self.is_active,
            'applicable_gasoline': self.applicable_gasoline,
            'applicable_diesel': self.applicable_diesel,
            'applicable_hev': self.applicable_hev,
            'applicable_phev': self.applicable_phev,
            'applicable_bev': self.applicable_bev,
            'sub_services': [s.to_dict() for s in (self.sub_services or [])] if self.sub_services else []
        }

# Many-to-many associations are stored as list fields (service_ids) on the
# owning documents (Appointment.service_ids, JobOrder.service_ids).
