from models import db, DocumentModel
from datetime import datetime


class ServiceDemandLog(DocumentModel):
    """Service Demand Log - historical service demand for predictive analytics."""
    __collection__ = 'service_demand_logs'

    def __init__(self, **kwargs):
        self.service_id = kwargs.pop('service_id', None)
        self.service_name = kwargs.pop('service_name', None)
        self.quantity_demanded = kwargs.pop('quantity_demanded', 0)
        self.date = kwargs.pop('date', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'service_id': self.service_id,
            'service_name': self.service_name,
            'quantity_demanded': self.quantity_demanded,
            'date': self.date.isoformat() if hasattr(self.date, 'isoformat') else self.date,
            'created_at': self.created_at.isoformat() if hasattr(self.created_at, 'isoformat') else self.created_at
        }


class InventoryUsageLog(DocumentModel):
    """Inventory Usage Log - historical inventory usage for analytics."""
    __collection__ = 'inventory_usage_logs'

    def __init__(self, **kwargs):
        self.inventory_item_id = kwargs.pop('inventory_item_id', None)
        self.item_name = kwargs.pop('item_name', None)
        self.quantity_used = kwargs.pop('quantity_used', 0)
        self.date = kwargs.pop('date', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'inventory_item_id': self.inventory_item_id,
            'item_name': self.item_name,
            'quantity_used': self.quantity_used,
            'date': self.date.isoformat() if hasattr(self.date, 'isoformat') else self.date,
            'created_at': self.created_at.isoformat() if hasattr(self.created_at, 'isoformat') else self.created_at
        }


class ForecastLog(DocumentModel):
    """Forecast Log - predictive analytics forecast data."""
    __collection__ = 'forecast_logs'

    def __init__(self, **kwargs):
        self.item_type = kwargs.pop('item_type', None)
        self.item_id = kwargs.pop('item_id', None)
        self.item_name = kwargs.pop('item_name', None)
        self.forecast_type = kwargs.pop('forecast_type', None)
        self.predicted_value = kwargs.pop('predicted_value', None)
        self.confidence_level = kwargs.pop('confidence_level', None)
        self.forecast_date = kwargs.pop('forecast_date', None)
        self.generated_at = kwargs.pop('generated_at', datetime.utcnow())
        self.notes = kwargs.pop('notes', None)
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id': self.id,
            'item_type': self.item_type,
            'item_id': self.item_id,
            'item_name': self.item_name,
            'forecast_type': self.forecast_type,
            'predicted_value': self.predicted_value,
            'confidence_level': self.confidence_level,
            'forecast_date': self.forecast_date.isoformat() if hasattr(self.forecast_date, 'isoformat') else self.forecast_date,
            'generated_at': self.generated_at.isoformat() if hasattr(self.generated_at, 'isoformat') else self.generated_at,
            'notes': self.notes
        }
