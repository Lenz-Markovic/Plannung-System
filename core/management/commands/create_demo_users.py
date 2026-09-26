"""
Create one test user per role, so you can try out the permissions.

    python manage.py create_demo_users --password "SomeLongPassword1"

Users: admin_demo, dispo_demo, sachbearbeitung_demo, ableser_demo, leitung_demo.
ONLY for local testing - never run this on a real server.
"""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import BaseCommand

from core import roles
from planning.models import Employee

DEMO_USERS = [
    ("admin_demo", roles.ADMIN),
    ("dispo_demo", roles.DISPATCHER),
    ("sachbearbeitung_demo", roles.PROCESSING),
    ("ableser_demo", roles.READER),
    ("leitung_demo", roles.MANAGEMENT),
]


class Command(BaseCommand):
    help = "Legt je Rolle einen Test-Benutzer an (nur lokal verwenden)."

    def add_arguments(self, parser):
        parser.add_argument("--password", required=True, help="Passwort für alle Test-Benutzer")

    def handle(self, *args, **options):
        call_command("setup_roles")  # make sure the groups exist
        for username, role in DEMO_USERS:
            user, _ = User.objects.get_or_create(username=username)
            user.set_password(options["password"])
            # Only the Admin role may open the Django admin (/admin/).
            user.is_staff = role == roles.ADMIN
            user.save()
            user.groups.set([Group.objects.get(name=role)])
            if role == roles.READER:
                Employee.objects.get_or_create(
                    user=user, defaults={"short_name": "Demo-Ableser", "can_read": True, "can_install": True}
                )
            self.stdout.write(f"{username} ({role})")
        self.stdout.write(self.style.SUCCESS("Test-Benutzer sind angelegt."))
