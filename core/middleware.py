class HtmxMiddleware:
    """Adds request.htmx and request.htmx_target to every request.

    HTMX sends the header "HX-Request: true" and, if the element has an
    hx-target, "HX-Target: <id>". Views use this to return only a part of
    the page instead of the whole page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.htmx = request.headers.get("HX-Request") == "true"
        request.htmx_target = request.headers.get("HX-Target", "") if request.htmx else ""
        return self.get_response(request)
