/* AutoMan shared auth-session helper — transparent Firebase ID token refresh.

   Firebase ID tokens expire exactly 1 hour after login, and (until now) this
   app had no way to renew one client-side: every authenticated page read the
   token out of sessionStorage once at load into a local variable and never
   updated it. Any tab left open past that hour started failing every
   request with 401 — admin's "failed to load", customers' bookings silently
   not being created, appointment lists coming back empty — until the person
   logged out and back in.

   This script transparently patches window.fetch sitewide, so no page needs
   to change how it calls fetch():
     - Before every authenticated request, the Authorization header is
       rewritten to whatever token currently lives in sessionStorage, so a
       page's own stale local copy is never actually what gets sent.
     - If a request still comes back 401, this exchanges the refresh token
       (saved by /login) for a new ID token via POST /api/auth/refresh and
       retries the request once.
     - It also refreshes proactively in the background every 50 minutes
       (tokens are good for 60), so active sessions rarely hit the 401 path
       at all.

   Include this BEFORE any other inline script that calls fetch():
       <script src="{{ url_for('static', filename='js/auth-session.js') }}"></script>
*/
(function () {
    'use strict';

    if (typeof window.fetch !== 'function') return;

    var originalFetch = window.fetch.bind(window);
    var refreshing = null; // in-flight refresh promise, shared by concurrent 401s

    function getToken() {
        try { return sessionStorage.getItem('token'); } catch (e) { return null; }
    }

    function getRefreshToken() {
        try { return sessionStorage.getItem('refresh_token'); } catch (e) { return null; }
    }

    function getSessionId() {
        try { return sessionStorage.getItem('session_id'); } catch (e) { return null; }
    }

    function storeTokens(accessToken, refreshToken) {
        try {
            if (accessToken) sessionStorage.setItem('token', accessToken);
            if (refreshToken) sessionStorage.setItem('refresh_token', refreshToken);
        } catch (e) { /* private browsing / storage full — nothing we can do */ }
    }

    function hasAuthHeader(init) {
        var headers = init && init.headers;
        if (!headers) return false;
        if (typeof Headers !== 'undefined' && headers instanceof Headers) {
            return headers.has('Authorization');
        }
        return Object.prototype.hasOwnProperty.call(headers, 'Authorization');
    }

    function setAuthHeader(init, token) {
        var headers = init && init.headers;
        if (!headers) return;
        if (typeof Headers !== 'undefined' && headers instanceof Headers) {
            headers.set('Authorization', 'Bearer ' + token);
        } else {
            headers['Authorization'] = 'Bearer ' + token;
        }
    }

    function setSessionHeader(init) {
        var headers = init && init.headers;
        var sid = getSessionId();
        if (!headers || !sid) return;
        if (typeof Headers !== 'undefined' && headers instanceof Headers) {
            headers.set('X-Session-Id', sid);
        } else {
            headers['X-Session-Id'] = sid;
        }
    }

    function handleSessionConflict(response) {
        if (response.status !== 401) return;
        try {
            response.clone().json().then(function (data) {
                if (!data || !data.session_conflict) return;
                try {
                    sessionStorage.removeItem('token');
                    sessionStorage.removeItem('refresh_token');
                    sessionStorage.removeItem('session_id');
                    sessionStorage.removeItem('user');
                } catch (e) { /* ignore */ }
                if (window.location.pathname.indexOf('/login') === -1) {
                    window.location.href = '/login?reason=elsewhere';
                }
            }).catch(function () {});
        } catch (e) { /* ignore */ }
    }

    function doRefresh() {
        if (refreshing) return refreshing;
        var rt = getRefreshToken();
        if (!rt) return Promise.resolve(null);

        refreshing = originalFetch('/api/auth/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: rt })
        }).then(function (resp) {
            return resp.ok ? resp.json() : null;
        }).then(function (data) {
            if (data && data.access_token) {
                storeTokens(data.access_token, data.refresh_token);
                return data.access_token;
            }
            return null;
        }).catch(function () {
            return null;
        }).then(function (result) {
            refreshing = null;
            return result;
        }, function (err) {
            refreshing = null;
            throw err;
        });
        return refreshing;
    }

    window.fetch = function (input, init) {
        var authed = hasAuthHeader(init);
        if (authed) {
            var current = getToken();
            if (current) setAuthHeader(init, current);
            setSessionHeader(init);
        }

        return originalFetch(input, init).then(function (response) {
            if (response.status !== 401 || !authed || !getRefreshToken()) {
                if (authed) handleSessionConflict(response);
                return response;
            }
            return doRefresh().then(function (newToken) {
                if (!newToken) return response; // couldn't refresh; surface the 401 as-is
                setAuthHeader(init, newToken);
                return originalFetch(input, init).then(function (retryResponse) {
                    handleSessionConflict(retryResponse);
                    return retryResponse;
                });
            });
        });
    };

    // Keep the token fresh in the background so active users rarely hit the
    // 401-retry path above at all.
    setInterval(function () {
        if (getRefreshToken()) doRefresh();
    }, 50 * 60 * 1000);
})();
