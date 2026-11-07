from django.conf import settings


def build_csp():
    parts = [f"{name} {' '.join(values)}" for name, values in settings.CSP_DIRECTIVES.items()]
    if settings.CSP_REPORT_URI:
        parts.append(f"report-uri {settings.CSP_REPORT_URI}")
    return "; ".join(parts)


class SecurityHeadersMiddleware:
    """Adds Content-Security-Policy (report-only by default) and Permissions-Policy.

    Settings are read on every request so they can be changed per environment (or overridden in tests).
    The Django admin is exempt because it ships its own inline assets.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not response.has_header("Permissions-Policy"):
            response["Permissions-Policy"] = settings.PERMISSIONS_POLICY
        mode = settings.CSP_MODE
        if mode in ("report-only", "enforce") and not request.path.startswith("/" + settings.ADMIN_URL):
            header = "Content-Security-Policy" if mode == "enforce" else "Content-Security-Policy-Report-Only"
            if not response.has_header(header):
                response[header] = build_csp()
        return response
