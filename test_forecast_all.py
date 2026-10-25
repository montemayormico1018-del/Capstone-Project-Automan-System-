from app import create_app, db
from routes.analytics import get_all_forecasts
from flask import Flask
import json

app = create_app('development')
with app.app_context():
    # Test the forecast-all endpoint
    response = get_all_forecasts()
    data = response[0].get_json()

    print('FORECAST-ALL RESULTS:')
    print(f'Total items: {data["count"]}')
    print(f'Items needing reorder: {data["items_needing_reorder"]}')
    print(f'Critical items: {data["critical_items"]}')
    print(f'Warning items: {data["warning_items"]}')
    print(f'Inventory movements: {data["inventory_movement_count"]}')
    print(f'Items with history: {data["items_with_history"]}')
    print()

    # Show a few sample forecasts
    print('SAMPLE FORECASTS:')
    for i, forecast in enumerate(data['forecasts'][:3]):
        print(f'{i+1}. {forecast["name"]}:')
        print(f'   Current stock: {forecast["current_stock"]}')
        print(f'   Forecast: {forecast["forecast"]["forecasted_demand"]}')
        print(f'   Confidence: {forecast["forecast"]["confidence"]}')
        print(f'   Trend: {forecast["trend"]}')
        print()