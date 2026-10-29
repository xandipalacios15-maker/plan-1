class SecurityHeaders:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response["X-Frame-Options"] = "DENY"
        response["Referrer-Policy"] = "strict-origin"
        response["Cache-Control"] = "no-store"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response
