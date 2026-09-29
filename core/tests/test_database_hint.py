"""After an update without `migrate`: a clear German page instead of a crash."""

from django.db.utils import OperationalError
from django.test import RequestFactory

from core.middleware import DatabaseNotUpToDateMiddleware


def middleware():
    return DatabaseNotUpToDateMiddleware(lambda request: None)


def test_missing_column_shows_the_migrate_hint():
    request = RequestFactory().get("/montage/")
    response = middleware().process_exception(request, OperationalError("no such column: buildings_devicecategory.price"))
    assert response.status_code == 503
    assert "python manage.py migrate" in response.content.decode()


def test_other_errors_are_not_hidden():
    request = RequestFactory().get("/montage/")
    assert middleware().process_exception(request, OperationalError("database is locked")) is None
    assert middleware().process_exception(request, ValueError("x")) is None
