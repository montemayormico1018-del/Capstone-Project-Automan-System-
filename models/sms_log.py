from models import db, DocumentModel
from datetime import datetime


class SMSLog(DocumentModel):
    """SMS Log - stores SMS notifications via iProg."""
    __collection__ = 'sms_logs'

    def __init__(self, **kwargs):
        self.phone_number = kwargs.pop('phone_number', None)
        self.message = kwargs.pop('message', None)
        self.status = kwargs.pop('status', 'PENDING')
        self.related_appointment_id = kwargs.pop('related_appointment_id', None)
        self.related_job_order_id = kwargs.pop('related_job_order_id', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'phone_number': self.phone_number,
            'message': self.message,
            'status': self.status,
            'related_appointment_id': self.related_appointment_id,
            'related_job_order_id': self.related_job_order_id,
            'created_at': self.created_at.isoformat() if hasattr(self.created_at, 'isoformat') else self.created_at
        }
