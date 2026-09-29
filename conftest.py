"""
Shared test fixtures.

demo_import: the prototype demo data (240 buildings, 70 orders, tours),
imported ONCE per test file instead of once per test - the import takes a
few seconds, so this makes the test run several times faster.

How it works (the same trick as Django's TestCase.setUpTestData): the import
runs inside a database transaction that stays open for the whole test file.
Every test runs inside its own savepoint and is rolled back afterwards, so
each test still starts with exactly the freshly imported data. At the end of
the file the import itself is rolled back, so other files start empty.
"""

import datetime
import io

import pytest
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

DEMO_TODAY = datetime.date(2026, 9, 28)


@pytest.fixture(scope="module")
def demo_import(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        outer = transaction.atomic()
        outer.__enter__()
        try:
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(timezone, "localdate", lambda *args: DEMO_TODAY)
                call_command("import_prototype", stdout=io.StringIO())
            yield
        finally:
            transaction.set_rollback(True)
            outer.__exit__(None, None, None)
