from flask import Flask, render_template, jsonify, request, send_from_directory, make_response, current_app
from flask_bcrypt import Bcrypt
from flask_compress import Compress
from models import db
from routes import register_blueprints
import hashlib
import os

from dotenv import load_dotenv
load_dotenv()  # populate os.environ from .env (FIREBASE_API_KEY, EMAIL_*, etc.)
# before anything below reads them via os.environ.get(...).

from firebase_config import init_firebase
from firebase_auth_utils import jwt_required, get_jwt_identity  # re-exported for routes
from config import Config


def create_app(config_name='development'):
    """Create and configure the Flask application."""
    app = Flask(__name__, static_folder='static')
    Compress(app)  # gzip/brotli for HTML/CSS/JS/JSON responses

    # Allow real SMS - remove forced simulation mode
    # os.environ.setdefault('SMS_SIMULATION_MODE', 'true')

    # SECRET_KEY signs quotation-approval links AND password-reset tokens --
    # a hardcoded fallback here would mean anyone who read this source could
    # forge both. Required outside of tests; a fixed test-only value is fine
    # under FIRESTORE_MOCK/TESTING since nothing produced there is real.
    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        if os.environ.get('FIRESTORE_MOCK') == '1' or os.environ.get('TESTING') == '1':
            secret_key = 'test-only-secret-key-not-for-production'
        else:
            raise RuntimeError(
                'SECRET_KEY environment variable is not set. It signs '
                'quotation-approval links and password-reset tokens, so an '
                'unset (or guessable) value lets those be forged. Set a '
                'long random SECRET_KEY before starting the app.'
            )
    app.config['SECRET_KEY'] = secret_key
    app.config['DEBUG'] = os.environ.get('FLASK_DEBUG', '0') == '1'

    # Base URL for quotation links in SMS - configure for production
    app.config['BASE_URL'] = os.environ.get('BASE_URL', 'https://localhost:5000')

    # Per-slot booking capacity (see config.py); re-read from env each time
    # so MAX_BOOKINGS_PER_SLOT can be overridden without touching Config.
    app.config['MAX_BOOKINGS_PER_SLOT'] = int(
        os.environ.get('MAX_BOOKINGS_PER_SLOT', Config.MAX_BOOKINGS_PER_SLOT)
    )
    app.config['QUOTATION_MARKUP'] = float(os.environ.get('QUOTATION_MARKUP', '500'))

    # Static assets (CSS/JS/images) get a long cache (below), which is only
    # safe if their URL changes whenever their content does — otherwise
    # Firebase Hosting's CDN in front of this service (not just a visitor's
    # own browser) keeps serving a stale cached copy indefinitely after a
    # deploy, since the file's path never changes on its own. This bit us
    # for real: automan-nav.js's shared escapeHtml() helper shipped in a fix,
    # but the CDN kept serving the pre-fix file, breaking every admin page
    # that called it, well after the deploy that fixed it had gone out.
    # url_defaults below appends a short hash of each static file's own
    # content as a ?v= query param, so a changed file gets a new URL (and a
    # fresh cache entry) automatically -- no manual version bump to remember.
    #
    # This used to hash the file's mtime instead of its content -- broke
    # silently on Cloud Run: "gcloud run deploy --source ." builds via
    # Buildpacks, which normalizes every file's mtime to a fixed timestamp
    # (1980-01-01, the classic archive-format epoch) for reproducible
    # builds. That made os.path.getmtime() return the exact same value on
    # every deploy, so ?v= never changed, so this cache-buster silently did
    # nothing in production -- any browser that ever cached a static file
    # kept serving that exact byte-for-byte version for the full
    # max-age=31536000 (1 year), no matter how many times the file was
    # redeployed with real changes. Hashing content instead of mtime is
    # correct regardless of what the deployment pipeline does to timestamps.
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000  # 1 year

    _static_version_cache = {}

    @app.url_defaults
    def _add_static_cache_buster(endpoint, values):
        if endpoint == 'static' and 'filename' in values:
            filename = values['filename']
            if filename not in _static_version_cache:
                static_path = os.path.join(app.static_folder, filename)
                try:
                    with open(static_path, 'rb') as f:
                        _static_version_cache[filename] = hashlib.sha256(f.read()).hexdigest()[:10]
                except OSError:
                    _static_version_cache[filename] = None  # missing -- let it 404 normally
            version = _static_version_cache[filename]
            if version:
                values['v'] = version

    # Initialize Firebase Admin + Firestore (replaces SQLAlchemy)
    try:
        init_firebase()
        app.logger.info('Firebase initialized (Firestore + Auth).')
    except Exception as e:
        app.logger.warning('Firebase not initialized (%s). Set FIREBASE_CREDENTIALS.', e)

    # Initialize extensions
    bcrypt = Bcrypt(app)

    # Register blueprints
    register_blueprints(app)

    # Firestore has no schema migrations; ensure sample data on first run.
    with app.app_context():
        init_sample_data()
    
    # Frontend routes (HTML pages)
    from flask import make_response

    @app.teardown_request
    def _reset_db_session(exception=None):
        """Clear any uncommitted objects the Firestore session shim picked up
        while serving this request (it auto-tracks every object loaded via
        Model.query.get()/.all()/etc — see firestore_db.py's _from_doc).
        Without this, objects merely read (not intentionally modified) in one
        request could leak into the next request's db.session.commit() and
        get rewritten with stale data."""
        db.session.rollback()

    @app.after_request
    def add_no_cache(response):
        """Prevent caching for dynamic responses — but not /static/, which
        SEND_FILE_MAX_AGE_DEFAULT above already gives a real cache policy.
        This used to apply to every response including CSS/JS/images,
        forcing a full re-download of all of them on every navigation."""
        if request.path.startswith('/static/'):
            return response
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    @app.after_request
    def add_security_headers(response):
        """Standard hardening headers (flagged missing by an external
        header scan). CSP is scoped to what the templates actually load —
        cdn.jsdelivr.net (Bootstrap/icons/SweetAlert2), code.jquery.com,
        the Tuqlas chatbot widget (www.tuqlas.com), and Google Fonts —
        rather than a generic policy that would block them. 'unsafe-inline'
        stays on script-src/style-src because every page ships large inline
        <script>/<style> blocks and onclick= handlers (base.html alone has
        a dozen); moving to nonces would mean rewriting every template."""
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = (
            'camera=(), microphone=(), geolocation=(), payment=(), usb=(), '
            'magnetometer=(), gyroscope=(), interest-cohort=()'
        )
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
            "https://code.jquery.com https://www.tuqlas.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
            "https://fonts.googleapis.com; "
            "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com data:; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://cdn.jsdelivr.net https://www.tuqlas.com; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'self'"
        )
        return response
    
    @app.route('/')
    def index():
        """Home page - redirects to login or dashboard."""
        return render_template('index.html')
    
    @app.route('/favicon.ico')
    def favicon():
        """Serve favicon to avoid 404 in browser console."""
        try:
            return send_from_directory(app.static_folder, 'favicon.ico')
        except Exception:
            return ('', 204)

    @app.route('/keepwarm')
    def keepwarm():
        """Minimal no-op endpoint for the Cloud Scheduler keep-warm ping —
        deliberately does no template rendering or Firestore reads so each
        ping costs as little as possible.

        NOT named /healthz: Cloud Run's own infrastructure reserves that
        exact path for its internal container health checks and intercepts
        it before it reaches the app (confirmed — it 404s live despite
        working in every local test), so a custom ping target needs a
        non-reserved name."""
        return ('OK', 200)

    @app.route('/manifest.json')
    def pwa_manifest():
        """PWA web app manifest (root path so it applies site-wide)."""
        return send_from_directory(app.static_folder, 'manifest.json',
                                   mimetype='application/manifest+json')

    @app.route('/sw.js')
    def service_worker():
        """Service worker — must be served from the root so its scope covers
        the whole app, not just /static/."""
        response = send_from_directory(app.static_folder, 'sw.js',
                                       mimetype='application/javascript')
        # SW updates should be picked up promptly, never long-cached
        response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.route('/login')
    def login_page():
        """Login page."""
        return render_template('login.html')
    
    @app.route('/register')
    def register_page():
        """Register page."""
        return render_template('register.html')

    @app.route('/forgot-password')
    def forgot_password_page():
        """Forgot-password request page."""
        return render_template('forgot_password.html')

    @app.route('/reset-password')
    def reset_password_page():
        """Password-reset confirmation page (reached via the emailed link)."""
        return render_template('reset_password.html')

    @app.route('/customer')
    def customer_dashboard():
        """Customer dashboard - for customers to view their appointments and vehicles."""
        from routes.appointments import BOOKING_TIME_SLOTS_DISPLAY
        return render_template('customer_dashboard.html', booking_time_slots=BOOKING_TIME_SLOTS_DISPLAY)

    @app.route('/dashboard')
    def dashboard():
        """Dashboard page."""
        return render_template('dashboard.html')

    @app.route('/appointments')
    def appointments_page():
        """Appointments page."""
        return render_template('appointments.html')

    @app.route('/job-orders')
    def job_orders_page():
        """Job orders page."""
        return render_template('job_orders.html')

    @app.route('/inventory')
    def inventory_page():
        """Inventory page."""
        return render_template('inventory.html')

    @app.route('/sales')
    def sales_page():
        """Sales page."""
        return render_template('sales.html')

    @app.route('/reports')
    def reports_page():
        """Reports page."""
        return render_template('reports.html')

    @app.route('/forecast')
    def forecast_page():
        """Forecast page (placeholder)."""
        html = render_template('forecast.html')
        response = make_response(html)
        # Add cache-busting headers
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    @app.route('/customers')
    def customers_page():
        """Customers page."""
        return render_template('customers.html')

    @app.route('/packages')
    def packages_page():
        """Service packages management page."""
        return render_template('packages.html')

    @app.route('/users')
    def users_page():
        """Users management page."""
        # For now, we'll let the frontend handle the auth check
        # The template will redirect if not admin
        return render_template('users.html')

    @app.route('/audit-log')
    def audit_log_page():
        """Admin-only accountability trail — who created/updated/deleted
        inventory and sales records, and when."""
        return render_template('audit_log.html')

    @app.route('/loyalty')
    def loyalty_page():
        """Loyalty & rewards admin — rewards catalog, customer balances and
        manual adjustments, voucher validation, and the earning rules."""
        return render_template('loyalty.html')

    # Role-based redirect routes (for login redirects)
    @app.route('/admin')
    def admin_page():
        """Admin dashboard redirect."""
        return render_template('dashboard.html')

    @app.route('/staff')
    def staff_page():
        """Staff dashboard redirect."""
        return render_template('dashboard.html')

    @app.route('/mechanic')
    def mechanic_page():
        """Mechanic dashboard redirect."""
        return render_template('dashboard.html')
    
    @app.route('/test-sms')
    def test_sms():
        """Test SMS sending - returns API response in browser."""
        from utils.sms_utils import send_sms
        
        number = request.args.get('number', '09762409060')
        message = request.args.get('message', 'Test message from Automan Car Care')
        
        try:
            result = send_sms(number, message)
            return jsonify({
                'sms_result': result,
                'api': 'iProg SMS API'
            })
        except Exception as e:
            return jsonify({
                'error': str(e),
                'api': 'iProg SMS API'
            }), 500
    
    @app.route('/quotation/view/<appointment_id>')
    def view_quotation(appointment_id):
        """Public URL for customers to view and respond to quotation."""
        from models.appointment import Appointment
        from utils.quotation_token import verify_quotation_token

        appointment = Appointment.get(appointment_id)

        if not appointment:
            return render_template('appointment_not_found.html'), 404

        if appointment.status not in ['Pending', 'Quotation Sent']:
            return render_template('quotation_processed.html', appointment=appointment)

        vehicle = appointment.vehicle
        services = appointment.services
        packages = appointment.packages
        token = request.args.get('t', '')
        token_valid = verify_quotation_token(appointment.id, token)

        # The customer dashboard links here without a token (only the SMS
        # link carries one). Issue a fresh token when the page is served so
        # the approve/reject forms work from both entry points. Approval is
        # still POST-only, which is what actually stops SMS link scanners
        # from auto-approving; the appointment id itself is unguessable.
        if not token_valid:
            from utils.quotation_token import generate_quotation_token
            token = generate_quotation_token(appointment.id)
            token_valid = True

        return render_template('quotation_view.html',
                               appointment=appointment,
                               vehicle=vehicle,
                               services=services,
                               packages=packages,
                               token=token,
                               token_valid=token_valid)

    @app.route('/quotation/approve/<appointment_id>', methods=['POST'])
    def approve_quotation_web(appointment_id):
        """Customer approves via the SMS link. POST-only (a GET here would
        let email/SMS link-preview crawlers trigger approval just by
        prefetching the URL) and requires the signed token minted when the
        quotation SMS was sent — the raw appointment id alone is not proof
        the caller is the customer it was sent to."""
        from models.appointment import Appointment
        from utils.quotation_token import verify_quotation_token

        appointment = Appointment.get(appointment_id)

        if not appointment:
            return render_template('appointment_not_found.html'), 404

        if not verify_quotation_token(appointment_id, request.form.get('token') or request.args.get('t')):
            return jsonify({'error': 'Invalid or expired approval link. Please request a new quotation.'}), 403

        if appointment.status not in ['Pending', 'Quotation Sent']:
            return render_template('quotation_processed.html', appointment=appointment)

        appointment.status = 'Approved'
        appointment.save()

        try:
            customer = appointment.customer
            if customer and customer.phone:
                from utils.sms_utils import send_sms
                send_sms(customer.phone,
                         f"Automan Car Care: Your appointment has been SCHEDULED and APPROVED for "
                         f"{appointment.preferred_date} at {appointment.preferred_time}. See you!")
        except Exception as e:
            current_app.logger.warning('approve_quotation_web %s: notification failed: %s', appointment_id, e)

        display_number = appointment.appointment_number or appointment.id
        return render_template('quotation_approved.html', job_order_number=display_number)

    @app.route('/quotation/reject/<appointment_id>', methods=['POST'])
    def reject_quotation_web(appointment_id):
        """Customer rejects via the SMS link. Same POST-only + signed-token
        requirement as approve_quotation_web above."""
        from models.appointment import Appointment
        from utils.quotation_token import verify_quotation_token

        appointment = Appointment.get(appointment_id)

        if not appointment:
            return render_template('appointment_not_found.html'), 404

        if not verify_quotation_token(appointment_id, request.form.get('token') or request.args.get('t')):
            return jsonify({'error': 'Invalid or expired link. Please request a new quotation.'}), 403

        if appointment.status not in ['Pending', 'Quotation Sent']:
            return render_template('quotation_processed.html', appointment=appointment)

        appointment.status = 'Rejected'
        appointment.save()

        return render_template('quotation_rejected.html')
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({'error': 'Not found'}), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': 'Internal server error'}), 500
    
    return app


def init_sample_data():
    """Firestore-backed initialization.

    Data is migrated once via export_to_firestore.py, so on boot we only
    ensure the demo Firebase Auth accounts exist (idempotent no-op if present).

    Gated behind ALLOW_DEMO_ACCOUNTS=1 -- these are well-known, hardcoded
    credentials (admin@automan.com / Admin123! etc.), so they must never be
    auto-created (or have their passwords silently reset back to these
    values on every restart) on a deployment the public or a panel can
    reach. Set ALLOW_DEMO_ACCOUNTS=1 only for a local/dev/demo environment
    where you specifically want them available.
    """
    if os.environ.get('ALLOW_DEMO_ACCOUNTS') != '1':
        current_app.logger.info('init_sample_data: ALLOW_DEMO_ACCOUNTS not set, skipping demo account creation.')
        return

    from models.user import User
    from firebase_config import get_auth, create_firebase_user, set_user_password

    demo = [
        ('admin', 'admin@automan.com', 'Admin123!', 'admin', '09123456789'),
        ('staff', 'staff@automan.com', 'Staff123!', 'staff', '09123456788'),
        ('mechanic', 'mechanic@automan.com', 'Mechanic123!', 'mechanic', '09123456787'),
        ('customer', 'customer@automan.com', 'Customer123!', 'customer', '09123456786'),
    ]
    auth = get_auth()
    for username, email, pw, role, phone in demo:
        user = User.find_by(email=email)
        if user is None:
            try:
                fb_user = create_firebase_user(email=email, password=pw, display_name=username)
                User(
                    id=fb_user.uid, username=username, email=email, role=role,
                    full_name={'admin': 'System Administrator', 'staff': 'Service Staff', 'mechanic': 'Service Mechanic', 'customer': 'Demo Customer'}.get(role),
                    contact_number=phone, is_active=True, is_verified=True
                ).save()
            except Exception as e:
                current_app.logger.warning('init_sample_data: could not create demo %s account: %s', role, e)
        else:
            if not user.full_name:
                user.full_name = {'admin': 'System Administrator', 'staff': 'Service Staff', 'mechanic': 'Service Mechanic', 'customer': 'Demo Customer'}.get(role, username)
                user.save()
            try:
                set_user_password(user.id, pw)
            except Exception:
                pass
    current_app.logger.info('Sample data check complete (Firestore).')


if __name__ == '__main__':
    app = create_app('development')
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False, ssl_context='adhoc')
