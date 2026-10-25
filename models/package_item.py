from models import db, DocumentModel
from datetime import datetime


class PackageItem(DocumentModel):
    """A single checklist row within a ServicePackage (e.g. "Check tire
    pressure & tread"). Pure checklist definition — completion state lives on
    JobOrder.completed_package_items, not here."""
    __collection__ = 'package_items'

    def __init__(self, **kwargs):
        self.package_id = kwargs.pop('package_id', None)
        self.item_name = kwargs.pop('item_name', None)
        self.item_description = kwargs.pop('item_description', None)
        self.sequence = kwargs.pop('sequence', 0)
        # Per-item vehicle applicability (e.g. "Engine oil change" doesn't
        # apply to a BEV) — same flag set/semantics as models/service.py.
        self.applicable_gasoline = kwargs.pop('applicable_gasoline', True)
        self.applicable_diesel = kwargs.pop('applicable_diesel', True)
        self.applicable_hev = kwargs.pop('applicable_hev', True)
        self.applicable_phev = kwargs.pop('applicable_phev', True)
        self.applicable_bev = kwargs.pop('applicable_bev', True)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'package_id': self.package_id,
            'item_name': self.item_name,
            'item_description': self.item_description,
            'sequence': self.sequence,
            'applicable_gasoline': self.applicable_gasoline,
            'applicable_diesel': self.applicable_diesel,
            'applicable_hev': self.applicable_hev,
            'applicable_phev': self.applicable_phev,
            'applicable_bev': self.applicable_bev,
        }
