from models import db, DocumentModel
from datetime import datetime

# See models/appointment.py's _UNSET for why: a job_order_id can point at a
# since-deleted document, and None can't double as both "not looked up yet"
# and "looked up, genuinely doesn't exist" without re-querying forever.
_UNSET = object()


class Sale(DocumentModel):
    """Sale model - generated from completed job orders."""
    __collection__ = 'sales'

    def __init__(self, **kwargs):
        self.job_order_id = kwargs.pop('job_order_id', None)
        self.invoice_number = kwargs.pop('invoice_number', None)
        self.subtotal = kwargs.pop('subtotal', 0.0)
        self.tax = kwargs.pop('tax', 0.0)
        self.discount = kwargs.pop('discount', 0.0)
        self.total = kwargs.pop('total', 0.0)
        self.payment_status = kwargs.pop('payment_status', 'Pending')
        self.payment_method = kwargs.pop('payment_method', None)
        self.amount_paid = kwargs.pop('amount_paid', 0.0)
        # Loyalty voucher applied to this sale. voucher_discount is kept as
        # its own field (rather than folded into `discount`) so the points
        # earning basis can subtract it explicitly -- see
        # utils/loyalty_service.earning_base_amount. calculate_total() below
        # does subtract it, since it goes into `discount`; the loyalty rule
        # un-folds that before applying its own formula.
        self.voucher_code = kwargs.pop('voucher_code', None)
        self.voucher_discount = kwargs.pop('voucher_discount', 0.0)
        # Portion of this sale settled with loyalty points. Excluded from the
        # earning basis (spending points shouldn't earn points back). Points
        # are not a payment method yet -- this is an exclusion field only.
        self.points_redeemed_amount = kwargs.pop('points_redeemed_amount', 0.0)
        self.notes = kwargs.pop('notes', None)
        # Soft delete / cancellation
        self.is_deleted = kwargs.pop('is_deleted', False)
        self.deleted_at = kwargs.pop('deleted_at', None)
        self.deleted_reason = kwargs.pop('deleted_reason', None)
        self.is_cancelled = kwargs.pop('is_cancelled', False)
        self.cancelled_at = kwargs.pop('cancelled_at', None)
        self.cancelled_reason = kwargs.pop('cancelled_reason', None)
        self.cancelled_by_user_id = kwargs.pop('cancelled_by_user_id', None)
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        self._items = kwargs.pop('items', None)
        self._job_order = kwargs.pop('job_order', _UNSET)
        super().__init__(**kwargs)

    @property
    def balance_due(self):
        return (self.total or 0.0) - (self.amount_paid or 0.0)

    @property
    def is_paid(self):
        return self.payment_status == 'Paid' or (self.amount_paid or 0) >= (self.total or 0)

    def calculate_total(self):
        self.subtotal = sum(item.total_price for item in self.items)
        self.total = self.subtotal + (self.tax or 0.0) - (self.discount or 0.0)

    @property
    def job_order(self):
        from models.job_order import JobOrder
        if self._job_order is not _UNSET:
            return self._job_order
        self._job_order = JobOrder.get(self.job_order_id) if self.job_order_id else None
        return self._job_order

    @property
    def items(self):
        if self._items is not None:
            return self._items
        self._items = SaleItem.query.filter_by(sale_id=self.id).all()
        return self._items

    @classmethod
    def prefetch(cls, sales):
        """Batch-load job_order (and, through it, vehicle/customer/
        appointment/services/packages) and items for a list of sales.

        Without this, Sale.to_dict() makes roughly half a dozen Firestore
        round trips per sale (job_order, its vehicle, that vehicle's
        customer, the appointment, its services/packages, and this sale's
        own items) — for a handful of rows on the Sales page that alone
        was dozens of sequential round trips before anything rendered.
        """
        from models.job_order import JobOrder

        sales = [s for s in sales if s is not None]
        if not sales:
            return sales

        job_orders = JobOrder.get_many(s.job_order_id for s in sales)
        JobOrder.prefetch(job_orders.values())

        items_by_sale = {}
        ids = [s.id for s in sales if s.id]
        for i in range(0, len(ids), 30):  # Firestore 'in' filter cap
            chunk = ids[i:i + 30]
            for item in SaleItem.query.filter(SaleItem.sale_id.in_(chunk)).all():
                items_by_sale.setdefault(item.sale_id, []).append(item)

        for s in sales:
            s._job_order = job_orders.get(s.job_order_id)
            s._items = items_by_sale.get(s.id, [])
        return sales

    @property
    def parts_cost(self):
        """Cost basis for profit: parts are marked up (sale price vs
        InventoryItem.cost); labor has no cost basis in this system (no
        mechanic wage/hour rate tracked), so it contributes 0 cost here —
        see .profit, which is total charged minus what the parts cost us."""
        jo = self.job_order
        if not jo:
            return 0.0
        return sum(
            (item.quantity_used or 0) * (item.inventory_item.cost or 0)
            for item in jo.items if item.inventory_item
        )

    @property
    def profit(self):
        return (self.total or 0.0) - self.parts_cost

    def to_dict(self):
        customer_name = None
        vehicle_info = None
        vehicle_plate = None
        job_order_number = None
        services_list = []
        appointment_date = None
        date_performed = None
        jo = self.job_order
        if jo:
            job_order_number = jo.job_order_number
            if jo.vehicle and jo.vehicle.customer:
                customer_name = jo.vehicle.customer.name
            if jo.vehicle:
                vehicle_info = f"{jo.vehicle.make} {jo.vehicle.model}"
                vehicle_plate = jo.vehicle.license_plate
            if not vehicle_plate and jo.appointment:
                vehicle_plate = jo.appointment.plate_number
            if jo.appointment:
                if jo.appointment.services:
                    services_list = [s.name for s in jo.appointment.services]
                # Packages count as performed work too (e.g. 21-Point Checkup)
                if jo.appointment.packages:
                    services_list += [f"{p.name} (Package)" for p in jo.appointment.packages]
                # When the customer originally asked to be booked in.
                pd = jo.appointment.preferred_date
                appointment_date = pd.isoformat() if hasattr(pd, 'isoformat') else pd
            # When the work actually got finished, as distinct from
            # appointment_date (booked) and created_at (invoice generated).
            date_performed = jo.completed_at.isoformat() if hasattr(jo.completed_at, 'isoformat') else jo.completed_at

        return {
            'id': self.id,
            'job_order_id': self.job_order_id,
            'invoice_number': self.invoice_number,
            'subtotal': self.subtotal,
            'tax': self.tax,
            'discount': self.discount,
            'total': self.total,
            'payment_status': self.payment_status,
            'payment_method': self.payment_method,
            'amount_paid': self.amount_paid,
            'balance_due': self.balance_due,
            'is_paid': self.is_paid,
            'voucher_code': self.voucher_code,
            'voucher_discount': self.voucher_discount,
            'points_redeemed_amount': self.points_redeemed_amount,
            'parts_cost': round(self.parts_cost, 2),
            'profit': round(self.profit, 2),
            'appointment_date': appointment_date,
            'date_performed': date_performed,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if hasattr(self.created_at, 'isoformat') else self.created_at,
            'items': [item.to_dict() for item in self.items],
            'customer_name': customer_name,
            'vehicle_info': vehicle_info,
            'vehicle_plate': vehicle_plate,
            'job_order_number': job_order_number,
            'services': services_list,
            'job_order': jo.to_dict(include_appointment=False, include_sale=False) if jo else None
        }


class SaleItem(DocumentModel):
    """Sale Item - individual items in a sale."""
    __collection__ = 'sale_items'

    def __init__(self, **kwargs):
        self.sale_id = kwargs.pop('sale_id', None)
        self.item_name = kwargs.pop('item_name', None)
        self.item_type = kwargs.pop('item_type', None)  # Service or Part
        self.quantity = kwargs.pop('quantity', None)
        self.unit_price = kwargs.pop('unit_price', None)
        super().__init__(**kwargs)

    @property
    def total_price(self):
        return (self.quantity or 0) * (self.unit_price or 0)

    def to_dict(self):
        return {
            'id': self.id,
            'sale_id': self.sale_id,
            'item_name': self.item_name,
            'item_type': self.item_type,
            'quantity': self.quantity,
            'unit_price': self.unit_price,
            'total_price': self.total_price
        }
