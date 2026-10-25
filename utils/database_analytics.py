"""
Inventory analytics and forecasting helpers, backed by Firestore.

Cloud Firestore has no server-side JOINs, GROUP BY, or aggregate functions
(func.sum/avg/count, CASE, strftime, etc.), so all of this reads the relevant
documents and aggregates them in Python. That mirrors the rest of the
Firestore data layer (see firestore_db.py) and is fine for this dataset size.
"""

from models.inventory import InventoryItem
from models.enhanced_models import InventoryMovement
from datetime import datetime, timedelta
import statistics


def _as_date(value):
    """Normalize a stored datetime/date/ISO-string value to a plain date.

    Firestore round-trips dates as ISO strings on older documents, so string
    values must be parsed before comparing against date objects.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).date()
        except ValueError:
            return None
    return value


class InventoryAnalytics:
    """Inventory analysis and forecasting utilities"""

    @staticmethod
    def get_items_below_reorder_level():
        """Get items that need reordering"""
        items = InventoryItem.query.filter_by(is_active=True).all()
        below = [
            i for i in items
            if not i.is_deleted and (i.stock or 0) <= (i.reorder_level or 0)
        ]
        below.sort(key=lambda i: (i.reorder_level or 0) - (i.stock or 0), reverse=True)
        return [
            (i.id, i.name, i.sku, i.stock, i.reorder_level,
             (i.reorder_level or 0) - (i.stock or 0), i.supplier)
            for i in below
        ]

    @staticmethod
    def get_monthly_usage(inventory_item_id, months=6, _cache=None):
        """
        Get monthly usage for an item with gap-filling (zero-fill missing months)

        Args:
            inventory_item_id: Item ID
            months: Number of months to look back (default 6)
            _cache: Optional dict shared across a batch of calls (e.g. one
                request forecasting every inventory item). Every caller in
                this class below — calculate_average_monthly_demand,
                forecast_next_month_demand, calculate_reorder_point,
                get_stock_trend_analysis — ends up calling this for the same
                (item, months) up to 4 times each. Without a shared cache,
                forecasting 21 items took 83 Firestore queries (~8.5s);
                passing the same dict through all of them cuts that to one
                query per item. None (the default) preserves the old
                behavior for standalone callers.
        """
        cache_key = (str(inventory_item_id), months)
        if _cache is not None and cache_key in _cache:
            return _cache[cache_key]

        today = datetime.utcnow().date()
        start_date = today - timedelta(days=30 * months)

        # A caller forecasting every item (e.g. forecast-all) may have
        # already fetched the whole movements collection once for its own
        # summary stats — if it hands that over pre-grouped by item under
        # ('movements', item_id), reuse it instead of one more query per item.
        movements_key = ('movements', str(inventory_item_id))
        if _cache is not None and movements_key in _cache:
            movements = _cache[movements_key]
        else:
            movements = InventoryMovement.query.filter_by(inventory_item_id=inventory_item_id).all()
        relevant = [
            m for m in movements
            if not m.is_cancelled
            and m.movement_type in ('OUT', 'WASTAGE')
            and _as_date(m.movement_date) is not None
            and _as_date(m.movement_date) >= start_date
        ]

        # Aggregate quantity/cost per month
        totals = {}
        for m in relevant:
            month_key = _as_date(m.movement_date).strftime('%Y-%m')
            qty, cost = totals.get(month_key, (0, 0.0))
            qty += m.quantity or 0
            cost += (m.quantity or 0) * (m.unit_price or 0)
            totals[month_key] = (qty, cost)

        # Generate complete month range and fill gaps with zeros
        result = []
        current = start_date.replace(day=1)  # Start of month
        end = today.replace(day=1)  # Current month

        try:
            from dateutil.relativedelta import relativedelta
            while current <= end:
                month_str = current.strftime('%Y-%m')
                qty, cost = totals.get(month_str, (0, 0))
                result.append((month_str, qty, cost))
                current += relativedelta(months=1)
        except ImportError:
            # Fallback without dateutil - just return what we have
            result = [(k, v[0], v[1]) for k, v in sorted(totals.items())]

        if _cache is not None:
            _cache[cache_key] = result
        return result

    @staticmethod
    def calculate_average_monthly_demand(inventory_item_id, months=6, _cache=None):
        """
        Calculate average monthly demand with gap-filled data

        Args:
            inventory_item_id: Item ID
            months: Number of months to analyze
            _cache: See get_monthly_usage.

        Returns:
            Dictionary with statistics including gap-filled data
        """
        monthly_data = InventoryAnalytics.get_monthly_usage(inventory_item_id, months, _cache)

        if not monthly_data:
            return {
                'avg_monthly_demand': 0,
                'stddev': 0,
                'min_usage': 0,
                'max_usage': 0,
                'months_active': 0,
                'months_total': 0
            }

        # Get all quantities (including zeros for gap-filled months)
        quantities = [m[1] if m[1] else 0 for m in monthly_data]
        months_total = len(quantities)

        # Get active months (non-zero)
        active_quantities = [q for q in quantities if q > 0]
        months_active = len(active_quantities)

        if not active_quantities:
            return {
                'avg_monthly_demand': 0,
                'stddev': 0,
                'min_usage': 0,
                'max_usage': 0,
                'months_active': 0,
                'months_total': months_total
            }

        # Average over ALL months (including zeros), not just active ones
        avg = sum(quantities) / months_total if months_total > 0 else 0

        return {
            'avg_monthly_demand': round(avg, 2),
            'stddev': round(statistics.stdev(quantities), 2) if len(quantities) > 1 else 0,
            'min_usage': min(active_quantities),
            'max_usage': max(active_quantities),
            'months_active': months_active,
            'months_total': months_total
        }

    @staticmethod
    def forecast_next_month_demand(inventory_item_id, months=6, _cache=None):
        """
        Forecast next month's demand with trend adjustment

        Args:
            inventory_item_id: Item ID
            months: Historical months to use
            _cache: See get_monthly_usage.

        Returns:
            Dictionary with forecast and confidence (requires 12+ months for HIGH)
        """
        stats = InventoryAnalytics.calculate_average_monthly_demand(inventory_item_id, months, _cache)

        if stats['months_active'] == 0:
            return {
                'forecasted_demand': 0,
                'confidence': 'VERY_LOW',
                'lower_bound': 0,
                'upper_bound': 0,
                'notes': 'Insufficient historical data'
            }

        avg = stats['avg_monthly_demand']
        stddev = stats['stddev']
        months_total = stats.get('months_total', stats['months_active'])

        # Use 95% confidence interval (2 * stddev)
        lower = max(0, avg - (2 * stddev))
        upper = avg + (2 * stddev)

        # Get trend and apply multiplier
        trend = InventoryAnalytics.get_stock_trend_analysis(inventory_item_id, months, _cache)
        trend_multiplier = 1.0
        if trend == 'UP':
            trend_multiplier = 1.1  # 10% increase
        elif trend == 'DOWN':
            trend_multiplier = 0.9  # 10% decrease

        adjusted_avg = avg * trend_multiplier

        # Determine confidence based on:
        # 1. Months of data (require 12+ for HIGH)
        # 2. Coefficient of variation (data consistency)
        cv = (stddev / avg * 100) if avg > 0 else 100  # Coefficient of variation

        if months_total >= 12 and cv < 15:
            confidence = 'HIGH'
        elif months_total >= 6 and cv < 30:
            confidence = 'MEDIUM'
        elif months_total >= 3:
            confidence = 'LOW'
        else:
            confidence = 'VERY_LOW'

        return {
            'forecasted_demand': round(adjusted_avg, 0),
            'base_demand': round(avg, 0),
            'trend_adjusted': trend,
            'trend_multiplier': trend_multiplier,
            'confidence': confidence,
            'lower_bound': round(lower * trend_multiplier, 0),
            'upper_bound': round(upper * trend_multiplier, 0),
            'coefficient_of_variation': round(cv, 2),
            'months_of_data': months_total,
            'notes': f'Based on {months_total} months of data ({stats["months_active"]} active)'
        }

    @staticmethod
    def calculate_reorder_point(inventory_item_id, months=6, _cache=None, item=None):
        """
        Calculate suggested reorder point with alert levels
        Formula: (Average Monthly Demand × Lead Time in Months) + Safety Stock
        where Safety Stock = avg_demand × item.safety_stock_percentage

        Args:
            inventory_item_id: Item ID
            months: Historical months to use
            _cache: See get_monthly_usage.
            item: Optional already-fetched InventoryItem, so a caller that's
                already iterating the item list (e.g. forecast-all) doesn't
                pay a redundant InventoryItem.get() for a document it's
                already holding.

        Returns:
            Dictionary with reorder point and alert levels
        """
        item = item or InventoryItem.query.get(inventory_item_id)
        if not item:
            return None

        stats = InventoryAnalytics.calculate_average_monthly_demand(inventory_item_id, months, _cache)
        avg_demand = stats['avg_monthly_demand']

        if avg_demand == 0:
            return {
                'reorder_point': item.reorder_level,
                'warning_level': int(item.reorder_level * 0.75),
                'critical_level': int(item.reorder_level * 0.25),
                'safety_stock': 0,
                'notes': 'No historical data, using reorder_level'
            }

        # Use item's lead_time_days (default 7 days = ~0.23 months)
        lead_time_days = item.lead_time_days or 7
        lead_time_months = lead_time_days / 30.0

        # Use item's safety_stock_percentage (default 0.5 = 50%)
        safety_stock_pct = item.safety_stock_percentage or 0.5
        safety_stock = avg_demand * safety_stock_pct

        # Reorder point = demand during lead time + safety stock
        reorder_point = (avg_demand * lead_time_months) + safety_stock

        # Alert levels (escalation)
        warning_level = reorder_point * 0.75  # 75% of reorder point
        critical_level = reorder_point * 0.25  # 25% of reorder point

        return {
            'reorder_point': round(reorder_point, 0),
            'warning_level': round(warning_level, 0),
            'critical_level': round(critical_level, 0),
            'safety_stock': round(safety_stock, 0),
            'lead_time_days': lead_time_days,
            'lead_time_demand': round(avg_demand * lead_time_months, 0),
            'notes': f'Lead time: {lead_time_days} days, Safety: {safety_stock_pct*100:.0f}%'
        }

    @staticmethod
    def get_stock_trend_analysis(inventory_item_id, months=6, _cache=None):
        """
        Analyze stock trend

        Args:
            inventory_item_id: Item ID
            months: Historical months to use
            _cache: See get_monthly_usage.

        Returns:
            'UP' if demand is increasing, 'DOWN' if decreasing, 'STABLE' if steady
        """
        monthly_data = InventoryAnalytics.get_monthly_usage(inventory_item_id, months, _cache)

        if len(monthly_data) < 3:
            return 'INSUFFICIENT_DATA'

        quantities = [m[1] if m[1] else 0 for m in monthly_data[-6:]]

        # Compare first half vs second half
        midpoint = len(quantities) // 2
        if midpoint == 0:
            return 'INSUFFICIENT_DATA'

        first_half_avg = sum(quantities[:midpoint]) / midpoint
        second_half_avg = sum(quantities[midpoint:]) / len(quantities[midpoint:])

        if first_half_avg == 0 and second_half_avg == 0:
            return 'STABLE'

        change_percent = ((second_half_avg - first_half_avg) / (first_half_avg + 0.01)) * 100

        if change_percent > 10:
            return 'UP'
        elif change_percent < -10:
            return 'DOWN'
        else:
            return 'STABLE'

    @staticmethod
    def get_all_items_needing_forecast():
        """Get all active items with usage history for forecasting"""
        return InventoryItem.query.filter_by(is_active=True).all()

    @staticmethod
    def generate_inventory_report(months=6):
        """Generate comprehensive inventory report with alert escalation"""
        items = InventoryAnalytics.get_all_items_needing_forecast()

        report = {
            'generated_at': datetime.utcnow().isoformat(),
            'period_months': months,
            'items': [],
            'critical_items': [],
            'warning_items': []
        }

        _cache = {}
        for item in items:
            stats = InventoryAnalytics.calculate_average_monthly_demand(item.id, months, _cache)
            forecast = InventoryAnalytics.forecast_next_month_demand(item.id, months, _cache)
            reorder_point = InventoryAnalytics.calculate_reorder_point(item.id, months, _cache, item)
            trend = InventoryAnalytics.get_stock_trend_analysis(item.id, months, _cache)

            # Determine alert level
            alert_level = 'OK'
            if isinstance(reorder_point, dict):
                if item.stock <= reorder_point['critical_level']:
                    alert_level = 'CRITICAL'
                elif item.stock <= reorder_point['warning_level']:
                    alert_level = 'WARNING'
                elif item.stock <= reorder_point['reorder_point']:
                    alert_level = 'REORDER'
            elif item.stock <= reorder_point:
                alert_level = 'REORDER'

            item_report = {
                'id': item.id,
                'name': item.name,
                'sku': item.sku,
                'category': item.category,
                'current_stock': item.stock,
                'reorder_level': item.reorder_level,
                'statistics': stats,
                'forecast': forecast,
                'suggested_reorder_point': reorder_point,
                'trend': trend,
                'alert_level': alert_level,
                'needs_reorder': item.stock <= item.reorder_level
            }

            report['items'].append(item_report)

            # Categorize critical and warning items
            if alert_level == 'CRITICAL':
                report['critical_items'].append(item_report)
            elif alert_level == 'WARNING':
                report['warning_items'].append(item_report)

        return report
