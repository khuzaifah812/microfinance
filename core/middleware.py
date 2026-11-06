import contextvars

_current_request = contextvars.ContextVar("current_request", default=None)


def get_current_request():
    return _current_request.get()


class RequestContextMiddleware:
    """Exposes the request to services (audit log needs user + IP without plumbing)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            _current_request.reset(token)


CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' https://fonts.googleapis.com; "
       "style-src-attr 'unsafe-inline'; font-src https://fonts.gstatic.com; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'; object-src 'none'")


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resp = self.get_response(request)
        resp.setdefault("Content-Security-Policy", CSP)
        resp.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        if not request.path.startswith("/static/"):
            resp["Cache-Control"] = "no-store, private"
        return resp
