"""Production entry point.

Run with: gunicorn -b 0.0.0.0:$PORT wsgi:app
Local development still uses: python app.py
"""
from app import create_app

app = create_app('production')
