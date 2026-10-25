from models import db, DocumentModel
from datetime import datetime


class ServicePackage(DocumentModel):
    """Bundled service package (e.g. "21-Point Checkup Package") with an
    itemized checklist of included work (see PackageItem)."""
    __collection__ = 'service_packages'

    def __init__(self, **kwargs):
        self.name = kwargs.pop('name', None)
        self.description = kwargs.pop('description', None)
        self.base_price = kwargs.pop('base_price', 0.0)
        # Optional per-vehicle-size-class overrides. base_price doubles as the
        # 'midsize' price; small/large fall back to base_price when unset, so
        # existing packages keep working unchanged until an admin sets them.
        self.price_small = kwargs.pop('price_small', None)
        self.price_large = kwargs.pop('price_large', None)
        self.estimated_duration_minutes = kwargs.pop('estimated_duration_minutes', None)
        self.is_active = kwargs.pop('is_active', True)
        # Package-level vehicle applicability (e.g. an entire package could be
        # gasoline-only). Separate from PackageItem's own per-item flags,
        # which filter individual checklist rows within an applicable package.
        self.applicable_gasoline = kwargs.pop('applicable_gasoline', True)
        self.applicable_diesel = kwargs.pop('applicable_diesel', True)
        self.applicable_hev = kwargs.pop('applicable_hev', True)
        self.applicable_phev = kwargs.pop('applicable_phev', True)
        self.applicable_bev = kwargs.pop('applicable_bev', True)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        # In-memory only
        self._items = kwargs.pop('items', None)
        super().__init__(**kwargs)

    @property
    def items(self):
        """Full, unfiltered checklist (used by the admin management UI)."""
        from models.package_item import PackageItem
        if self._items is not None:
            return self._items
        self._items = PackageItem.query.filter_by(package_id=self.id).all()
        self._items.sort(key=lambda it: (it.sequence if it.sequence is not None else 0))
        return self._items

    @classmethod
    def prefetch(cls, packages):
        """Batch-load checklist items for a list of packages.

        Without this, each package's `items` property (used by to_dict())
        makes its own PackageItem query — the packages list page was one
        round trip per package just to render its checklist.
        """
        from models.package_item import PackageItem

        packages = [p for p in packages if p is not None]
        if not packages:
            return packages

        items_by_package = {}
        ids = [p.id for p in packages if p.id]
        for i in range(0, len(ids), 30):  # Firestore 'in' filter cap
            chunk = ids[i:i + 30]
            for item in PackageItem.query.filter(PackageItem.package_id.in_(chunk)).all():
                items_by_package.setdefault(item.package_id, []).append(item)

        for p in packages:
            items = items_by_package.get(p.id, [])
            items.sort(key=lambda it: (it.sequence if it.sequence is not None else 0))
            p._items = items
        return packages

    def price_for(self, size_class):
        """Price for a given vehicle size class ('small'/'midsize'/'large'),
        falling back to base_price when no override is set for that class."""
        if size_class == 'small' and self.price_small is not None:
            return self.price_small
        if size_class == 'large' and self.price_large is not None:
            return self.price_large
        return self.base_price

    def applicable_items(self, vehicle):
        """Checklist filtered to what actually applies to a given vehicle's
        powertrain (e.g. excludes an oil-change item for a BEV)."""
        if vehicle is None:
            return self.items
        from utils.vehicle_applicability import (
            normalize_vehicle_powertrain, is_service_applicable_by_flag,
        )
        powertrain = normalize_vehicle_powertrain(vehicle)
        return [item for item in self.items if is_service_applicable_by_flag(item, powertrain)]

    def to_dict(self, include_items=True, vehicle=None):
        d = {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'base_price': self.base_price,
            'price_small': self.price_small,
            'price_large': self.price_large,
            'estimated_duration_minutes': self.estimated_duration_minutes,
            'is_active': self.is_active,
            'applicable_gasoline': self.applicable_gasoline,
            'applicable_diesel': self.applicable_diesel,
            'applicable_hev': self.applicable_hev,
            'applicable_phev': self.applicable_phev,
            'applicable_bev': self.applicable_bev,
        }
        if include_items:
            items = self.applicable_items(vehicle) if vehicle is not None else self.items
            d['items'] = [i.to_dict() for i in items]
        return d
