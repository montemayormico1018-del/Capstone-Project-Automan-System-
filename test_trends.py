from app import create_app, db
from utils.database_analytics import InventoryAnalytics
from models.inventory import InventoryItem

app = create_app('development')
with app.app_context():
    # Find an item with UP trend
    items = InventoryItem.query.all()
    up_item = None
    down_item = None

    for item in items:
        trend = InventoryAnalytics.get_stock_trend_analysis(item.id, months=6)
        if trend == 'UP' and not up_item:
            up_item = item
        elif trend == 'DOWN' and not down_item:
            down_item = item

    if up_item:
        print(f'UP TREND EXAMPLE: {up_item.name}')
        forecast = InventoryAnalytics.forecast_next_month_demand(up_item.id, months=6)
        print(f'  Base demand: {forecast["base_demand"]}')
        print(f'  Trend multiplier: {forecast["trend_multiplier"]} (should be 1.1)')
        print(f'  Adjusted forecast: {forecast["forecasted_demand"]}')
        print(f'  Expected: {forecast["base_demand"] * 1.1} = {forecast["base_demand"] * 1.1}')
        print()

    if down_item:
        print(f'DOWN TREND EXAMPLE: {down_item.name}')
        forecast = InventoryAnalytics.forecast_next_month_demand(down_item.id, months=6)
        print(f'  Base demand: {forecast["base_demand"]}')
        print(f'  Trend multiplier: {forecast["trend_multiplier"]} (should be 0.9)')
        print(f'  Adjusted forecast: {forecast["forecasted_demand"]}')
        print(f'  Expected: {forecast["base_demand"] * 0.9} = {forecast["base_demand"] * 0.9}')