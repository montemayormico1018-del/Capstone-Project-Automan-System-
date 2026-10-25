from models import db, DocumentModel
from datetime import datetime


class AuditLog(DocumentModel):
    """Immutable record of who did what and when.

    Written for accountability on sensitive, business-impacting actions
    (inventory and sales create/update/delete to start) — never edited or
    deleted after creation.
    """
    __collection__ = 'audit_logs'

    def __init__(self, **kwargs):
        self.user_id = kwargs.pop('user_id', None)
        self.username = kwargs.pop('username', None)
        self.role = kwargs.pop('role', None)
        self.action = kwargs.pop('action', None)  # 'create', 'update', 'delete'
        self.entity_type = kwargs.pop('entity_type', None)  # e.g. 'inventory_item', 'sale'
        self.entity_id = kwargs.pop('entity_id', None)
        self.description = kwargs.pop('description', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'role': self.role,
            'action': self.action,
            'entity_type': self.entity_type,
            'entity_id': self.entity_id,
            'description': self.description,
            'created_at': self.created_at.isoformat() if hasattr(self.created_at, 'isoformat') else self.created_at
        }
