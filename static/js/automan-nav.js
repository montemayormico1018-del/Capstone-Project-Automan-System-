/* AutoMan shared sidebar/nav — the ONE source of truth for role-based
   navigation. Replaces the navItems arrays that were copy-pasted (with
   drift) into every admin template.

   Usage: include BEFORE the page's inline script:
       <script src="/static/js/automan-nav.js"></script>
   Requires in the page markup: <nav id="sidebarNav"></nav>, and optionally
   #displayName / #displayRole spans and #sidebar / #sidebarOverlay for the
   mobile drawer. Builds automatically on DOMContentLoaded; pages may also
   call buildSidebar() themselves (harmless re-render). */

(function () {
    'use strict';

    // Shared HTML-escaping helper — every page that interpolates
    // server/user data into innerHTML (item names, notes, usernames, etc.)
    // must run it through this first, or a value like
    // <img src=x onerror=...> renders as a live element instead of text.
    // Escapes all five of & < > " ', not just the first three: a version
    // that only escapes & < > (e.g. a textContent->innerHTML round trip)
    // still leaves "/'-delimited attribute values (title="${...}",
    // value="${...}") breakable with a bare quote.
    window.escapeHtml = function escapeHtml(value) {
        if (value === null || value === undefined) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    };

    var NAV_ITEMS = {
        admin: [
            { href: '/dashboard',    icon: 'bi-speedometer2',    text: 'Dashboard' },
            { href: '/appointments', icon: 'bi-calendar-check',  text: 'Appointments' },
            { href: '/job-orders',   icon: 'bi-clipboard-check', text: 'Job Orders' },
            { href: '/inventory',    icon: 'bi-box-seam',        text: 'Inventory', stockAlert: true },
            { href: '/sales',        icon: 'bi-receipt',         text: 'Sales' },
            { href: '/customers',    icon: 'bi-people',          text: 'Customers' },
            { href: '/packages',     icon: 'bi-boxes',           text: 'Packages' },
            { href: '/reports',      icon: 'bi-bar-chart',       text: 'Reports' },
            { href: '/forecast',     icon: 'bi-graph-up-arrow',  text: 'Restocking Forecast', stockAlert: true },
            { href: '/users',        icon: 'bi-person-gear',     text: 'Users' },
            { href: '/loyalty',      icon: 'bi-gift',            text: 'Loyalty' },
            { href: '/audit-log',    icon: 'bi-journal-check',   text: 'Audit Log' }
        ],
        staff: [
            { href: '/dashboard',    icon: 'bi-speedometer2',    text: 'Dashboard' },
            { href: '/appointments', icon: 'bi-calendar-check',  text: 'Appointments' },
            { href: '/job-orders',   icon: 'bi-clipboard-check', text: 'Job Orders' },
            { href: '/inventory',    icon: 'bi-box-seam',        text: 'Inventory', stockAlert: true },
            { href: '/sales',        icon: 'bi-receipt',         text: 'Sales' },
            { href: '/customers',    icon: 'bi-people',          text: 'Customers' },
            { href: '/packages',     icon: 'bi-boxes',           text: 'Packages' },
            { href: '/reports',      icon: 'bi-bar-chart',       text: 'Reports' },
            { href: '/forecast',     icon: 'bi-graph-up-arrow',  text: 'Restocking Forecast', stockAlert: true },
            { href: '/loyalty',      icon: 'bi-gift',            text: 'Loyalty' }
        ],
        mechanic: [
            { href: '/dashboard',  icon: 'bi-speedometer2',    text: 'Dashboard' },
            { href: '/job-orders', icon: 'bi-clipboard-check', text: 'Job Orders' },
            { href: '/inventory',  icon: 'bi-box-seam',        text: 'Inventory', stockAlert: true },
            { href: '/forecast',   icon: 'bi-graph-up-arrow',  text: 'Restocking Forecast', stockAlert: true }
        ]
    };

    function currentUser() {
        // sessionStorage is per-tab (localStorage is shared across every tab
        // of the same origin, which let one tab's login silently overwrite
        // another's session).
        try {
            return JSON.parse(sessionStorage.getItem('user') || '{}');
        } catch (e) {
            return {};
        }
    }

    window.buildSidebar = function buildSidebar() {
        var nav = document.getElementById('sidebarNav');
        if (!nav) return;

        var user = currentUser();
        var role = (user.role || '').toLowerCase();
        var items = NAV_ITEMS[role] || NAV_ITEMS.staff;
        var path = window.location.pathname;

        nav.innerHTML = items.map(function (item) {
            var active = path === item.href ? 'active' : '';
            var dot = item.stockAlert ? '<span class="stock-alert-dot" hidden></span>' : '';
            return '<a href="' + item.href + '" class="' + active + '">' +
                   '<i class="bi ' + item.icon + ' me-2"></i> ' + item.text + dot + '</a>';
        }).join('');

        var nameEl = document.getElementById('displayName');
        if (nameEl) nameEl.textContent = user.full_name || user.username || '-';
        var roleEl = document.getElementById('displayRole');
        if (roleEl) roleEl.textContent = user.role || '-';

        if (nav.querySelector('.stock-alert-dot')) checkCriticalStock();
    };

    // Blinking red dot on Inventory/Restocking Forecast when at least one
    // item has fallen to or below the CRITICAL threshold (same 25%-of-
    // reorder-level rule as routes/inventory.py's /critical-count and
    // calculate_reorder_point()'s fallback -- one rule, cheap to check here).
    var _stockAlertCSSInjected = false;
    function _injectStockAlertCSS() {
        if (_stockAlertCSSInjected) return;
        _stockAlertCSSInjected = true;
        var style = document.createElement('style');
        style.textContent =
            '.stock-alert-dot { display: inline-block; width: 9px; height: 9px; ' +
            'border-radius: 50%; background: #e53935; margin-left: 8px; ' +
            'vertical-align: middle; box-shadow: 0 0 0 rgba(229,57,53,.6); ' +
            'animation: automanStockBlink 1.1s ease-in-out infinite; }' +
            '@keyframes automanStockBlink { ' +
            '0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(229,57,53,.6); } ' +
            '50% { opacity: .35; box-shadow: 0 0 6px 3px rgba(229,57,53,.3); } }';
        document.head.appendChild(style);
    }

    function checkCriticalStock() {
        var token = sessionStorage.getItem('token');
        if (!token) return;
        fetch('/api/inventory/critical-count', { headers: { 'Authorization': 'Bearer ' + token } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                var isCritical = !!(data && data.critical_count > 0);
                var dots = document.querySelectorAll('.stock-alert-dot');
                if (isCritical && dots.length) _injectStockAlertCSS();
                dots.forEach(function (dot) { dot.hidden = !isCritical; });
            })
            .catch(function () {});
    }

    // Re-checked periodically (stock doesn't change fast enough to need
    // the 5-10s polling used for job-order/appointment status elsewhere).
    setInterval(checkCriticalStock, 60000);

    window.toggleSidebar = function toggleSidebar() {
        var sidebar = document.getElementById('sidebar');
        var overlay = document.getElementById('sidebarOverlay');
        if (sidebar) sidebar.classList.toggle('show');
        if (overlay) overlay.classList.toggle('show');
    };

    window.logout = function logout() {
        try {
            var push = window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PushNotifications;
            var fcmToken = push && localStorage.getItem('fcmToken');
            var authToken = sessionStorage.getItem('token');
            if (fcmToken && authToken) {
                // keepalive so the request has a chance to land even as the
                // page navigates away right after this.
                fetch('/api/notifications/unregister-device', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + authToken },
                    body: JSON.stringify({ token: fcmToken }),
                    keepalive: true
                }).catch(function () {});
            }
        } catch (e) {}
        try { sessionStorage.clear(); } catch (e) {}
        localStorage.clear();
        window.location.href = '/login';
    };

    // Push notifications (Capacitor app only). window.Capacitor doesn't
    // exist in a normal browser tab, so this whole thing is a no-op there
    // -- same live site, no separate build for "the app version".
    window.registerPushNotifications = function registerPushNotifications() {
        var push = window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PushNotifications;
        if (!push) return;
        var authToken = sessionStorage.getItem('token');
        if (!authToken) return; // not logged in yet

        push.addListener('registration', function (tokenResult) {
            try { localStorage.setItem('fcmToken', tokenResult.value); } catch (e) {}
            fetch('/api/notifications/register-device', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + authToken },
                body: JSON.stringify({ token: tokenResult.value })
            }).catch(function () {});
        });
        push.addListener('registrationError', function (err) {
            console.error('Push registration error:', err);
        });

        // Android only auto-shows a system notification banner while the
        // app is BACKGROUNDED -- a message that arrives while the app is
        // open and in the foreground (the common case during a live demo)
        // shows nothing at all unless the app handles it itself. This is
        // that handling: a dismissible toast so a push is visible either way.
        push.addListener('pushNotificationReceived', function (notification) {
            var title = notification.title || 'AutoMan';
            var body = notification.body || '';
            if (typeof Swal !== 'undefined') {
                Swal.fire({
                    toast: true,
                    position: 'top-end',
                    icon: 'info',
                    title: title,
                    text: body,
                    showConfirmButton: false,
                    timer: 6000,
                    timerProgressBar: true
                });
            } else {
                alert(title + (body ? '\n' + body : ''));
            }
        });
        // Tapping the system notification (app was backgrounded) -- reload
        // so whatever changed server-side shows immediately.
        push.addListener('pushNotificationActionPerformed', function () {
            window.location.reload();
        });

        push.requestPermissions().then(function (result) {
            if (result.receive === 'granted') push.register();
        }).catch(function () {});
    };

    // Self-service "Change Password", injected into every page that includes
    // this script (rather than duplicated per-template) so admin/staff/
    // mechanic pages all get it from this one place. Finds the existing
    // sidebar Logout button and adds a matching button + modal right before it.
    window.buildChangePasswordUI = function buildChangePasswordUI() {
        if (document.getElementById('changePasswordModal')) return; // already injected

        var sidebar = document.getElementById('sidebar');
        if (!sidebar) return;
        var logoutBtn = sidebar.querySelector('button[onclick="logout()"]');
        if (!logoutBtn) return;

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-outline-light w-100 mb-2';
        btn.setAttribute('data-bs-toggle', 'modal');
        btn.setAttribute('data-bs-target', '#changePasswordModal');
        btn.innerHTML = '<i class="bi bi-key me-2"></i> Change Password';
        logoutBtn.insertAdjacentElement('beforebegin', btn);

        document.body.insertAdjacentHTML('beforeend',
            '<div class="modal fade" id="changePasswordModal" tabindex="-1">' +
              '<div class="modal-dialog modal-dialog-centered">' +
                '<div class="modal-content">' +
                  '<div class="modal-header" style="background:#0f0f10;">' +
                    '<h5 class="modal-title text-white"><i class="bi bi-key me-2"></i>Change Password</h5>' +
                    '<button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>' +
                  '</div>' +
                  '<div class="modal-body">' +
                    '<div id="changePasswordAlert"></div>' +
                    '<form id="changePasswordForm">' +
                      '<div class="mb-3">' +
                        '<label class="form-label fw-semibold">Current Password</label>' +
                        '<input type="password" class="form-control" id="currentPassword" required>' +
                      '</div>' +
                      '<div class="mb-3">' +
                        '<label class="form-label fw-semibold">New Password</label>' +
                        '<input type="password" class="form-control" id="newPassword" required>' +
                        '<div class="form-text">More than 6 characters, 1 uppercase letter, 1 special character.</div>' +
                      '</div>' +
                      '<div class="mb-3">' +
                        '<label class="form-label fw-semibold">Confirm New Password</label>' +
                        '<input type="password" class="form-control" id="confirmNewPassword" required>' +
                      '</div>' +
                    '</form>' +
                  '</div>' +
                  '<div class="modal-footer">' +
                    '<button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>' +
                    '<button type="button" class="btn btn-primary" id="changePasswordSubmitBtn" onclick="changePassword()">' +
                      '<i class="bi bi-check-circle me-1"></i>Update Password</button>' +
                  '</div>' +
                '</div>' +
              '</div>' +
            '</div>'
        );
    };

    window.changePassword = async function changePassword() {
        var currentPassword = document.getElementById('currentPassword').value;
        var newPassword = document.getElementById('newPassword').value;
        var confirmNewPassword = document.getElementById('confirmNewPassword').value;
        var alertBox = document.getElementById('changePasswordAlert');
        var submitBtn = document.getElementById('changePasswordSubmitBtn');
        alertBox.innerHTML = '';

        if (!currentPassword || !newPassword || !confirmNewPassword) {
            alertBox.innerHTML = '<div class="alert alert-danger">All fields are required</div>';
            return;
        }
        if (newPassword !== confirmNewPassword) {
            alertBox.innerHTML = '<div class="alert alert-danger">New passwords do not match</div>';
            return;
        }

        var token = sessionStorage.getItem('token');
        submitBtn.disabled = true;
        try {
            var res = await fetch('/api/auth/change-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
                body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
            });
            var data = await res.json();
            if (!res.ok) {
                alertBox.innerHTML = '<div class="alert alert-danger">' + window.escapeHtml(data.error || 'Something went wrong') + '</div>';
                return;
            }
            document.getElementById('changePasswordForm').reset();
            var modalEl = document.getElementById('changePasswordModal');
            if (window.bootstrap) {
                window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
            }
            if (typeof Swal !== 'undefined') {
                Swal.fire('Automan', data.message || 'Password changed successfully', 'success');
            } else {
                alert(data.message || 'Password changed successfully');
            }
        } catch (e) {
            alertBox.innerHTML = '<div class="alert alert-danger">Network error. Please try again.</div>';
        } finally {
            submitBtn.disabled = false;
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            window.buildSidebar();
            window.buildChangePasswordUI();
            window.registerPushNotifications();
        });
    } else {
        window.buildSidebar();
        window.buildChangePasswordUI();
        window.registerPushNotifications();
    }

    // PWA: register the service worker (served from root scope by app.py)
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', function () {
            navigator.serviceWorker.register('/sw.js').catch(function () {
                /* non-fatal — app works fine without the SW */
            });
        });
    }
})();
