from models import DocumentModel
from datetime import datetime


class SystemSetting(DocumentModel):
    """Small Cloud Firestore-backed settings document.

    A stable id (``pricing``) is used so configuration is shared by every
    Cloud Run instance instead of relying on a local environment value.
    """
    __collection__ = 'system_settings'

    def __init__(self, **kwargs):
        self.quotation_markup = kwargs.pop('quotation_markup', 500.0)
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        super().__init__(**kwargs)

    @classmethod
    def pricing(cls):
        setting = cls.get('pricing')
        if setting is None:
            setting = cls(id='pricing')
            setting.save()
        return setting

