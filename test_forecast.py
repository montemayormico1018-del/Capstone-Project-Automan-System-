from app import create_app, db
from utils.database_analytics import InventoryAnalytics
from models.inventory import InventoryItem

app = create_app('development')
with app.app_context():
    # Get a sample inventory item
    item = InventoryItem.query.first()
    if item:
        print(f'Testing forecast for: {item.name} (ID: {item.id})')
        print(f'Current stock: {item.stock}')
        print()

        # Test forecast
        forecast = InventoryAnalytics.forecast_next_month_demand(item.id, months=6)
        print('FORECAST RESULTS:')
        print(f'  Forecasted demand: {forecast["forecasted_demand"]}')
        print(f'  Base demand: {forecast["base_demand"]}')
        print(f'  Confidence: {forecast["confidence"]}')
        print(f'  Trend: {forecast["trend_adjusted"]}')
        print(f'  Trend multiplier: {forecast["trend_multiplier"]}')
        print(f'  Months of data: {forecast["months_of_data"]}')
        print()

        # Test reorder point
        reorder = InventoryAnalytics.calculate_reorder_point(item.id, months=6)
        print('REORDER POINT:')
        if isinstance(reorder, dict):
            print(f'  Reorder point: {reorder["reorder_point"]}')
            print(f'  Critical level: {reorder["critical_level"]}')
            print(f'  Warning level: {reorder["warning_level"]}')
        else:
            print(f'  Reorder point: {reorder}')
        print()

        # Test trend analysis
        trend = InventoryAnalytics.get_stock_trend_analysis(item.id, months=6)
        print(f'TREND ANALYSIS: {trend}')

    else:
        print('No inventory items found!')