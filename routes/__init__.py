from routes.auth import auth_bp
from routes.users import users_bp
from routes.appointments import appointments_bp
from routes.job_orders import job_orders_bp
from routes.inventory import inventory_bp
from routes.sales import sales_bp
from routes.reports import reports_bp
from routes.forecast import forecast_bp
from routes.analytics import analytics_bp
from routes.brands import brands_bp
from routes.packages import packages_bp
from routes.audit import audit_bp
from routes.notifications import notifications_bp
from routes.loyalty import loyalty_bp


def register_blueprints(app):
    """Register all blueprints with the Flask app."""
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(users_bp, url_prefix='/api/users')
    app.register_blueprint(appointments_bp, url_prefix='/api/appointments')
    app.register_blueprint(job_orders_bp, url_prefix='/api/job-orders')
    app.register_blueprint(inventory_bp, url_prefix='/api/inventory')
    app.register_blueprint(sales_bp, url_prefix='/api/sales')
    app.register_blueprint(reports_bp, url_prefix='/api/reports')
    app.register_blueprint(forecast_bp, url_prefix='/api/forecast')
    app.register_blueprint(analytics_bp, url_prefix='/api/analytics')
    app.register_blueprint(brands_bp, url_prefix='/api/brands')
    app.register_blueprint(packages_bp, url_prefix='/api/packages')
    app.register_blueprint(audit_bp, url_prefix='/api/audit-logs')
    app.register_blueprint(notifications_bp, url_prefix='/api/notifications')
    app.register_blueprint(loyalty_bp, url_prefix='/api/loyalty')
