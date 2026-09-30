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


class DatabaseNotUpToDateMiddleware:
    """A clear German page instead of a crash when `python manage.py migrate` was forgotten.

    After an update with new database fields, pages fail with "no such column" /
    "no such table" until the migrations have run. This page says what to do.
    """

    SIGNS = ("no such column", "no such table", "has no column named", "does not exist", "existiert nicht")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        from django.db.utils import OperationalError, ProgrammingError
        from django.http import HttpResponse

        if not isinstance(exception, (OperationalError, ProgrammingError)):
            return None
        text = str(exception).lower()
        if not any(sign in text for sign in self.SIGNS) or text.startswith(("database ", "role ", "datenbank ", "rolle ")):
            return None
        import logging
        logging.getLogger("django.request").warning("Datenbank nicht migriert: %s", exception, exc_info=exception)
        html = (
            "<!doctype html><html lang='de'><meta charset='utf-8'><title>Datenbank nicht aktuell</title>"
            "<body style='font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;line-height:1.5'>"
            "<h1>⚠ Die Datenbank ist nicht auf dem neuesten Stand</h1>"
            "<p>Nach dem letzten Update fehlen neue Felder in der Datenbank. "
            "Bitte im Terminal den Server stoppen (<b>Strg+C</b>) und dann eingeben:</p>"
            "<pre style='background:#f3f3f3;padding:12px;border-radius:6px'>python manage.py migrate\n"
            "python manage.py runserver</pre>"
            "<p>Danach diese Seite neu laden (<b>Strg+Umschalt+R</b>).</p></body></html>"
        )
        return HttpResponse(html, status=503)
