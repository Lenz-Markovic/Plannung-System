"""
Create the five role groups and give them their default permissions.

    python manage.py setup_roles          # add missing groups/permissions
    python manage.py setup_roles --reset  # set permissions exactly as in core/roles.py

Without --reset, permissions that an admin added in the admin area are kept.
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError

from core.roles import ALL_PERMISSIONS, ROLE_PERMISSIONS


def find_permissions(names):
    """Turn ["app_label.codename", ...] into Permission objects."""
    permissions = []
    for name in names:
        app_label, codename = name.split(".")
        try:
            permissions.append(
                Permission.objects.get(content_type__app_label=app_label, codename=codename)
            )
        except Permission.DoesNotExist:
            raise CommandError(f"Unknown permission '{name}'. Did you run 'migrate'?")
    return permissions


class Command(BaseCommand):
    help = "Legt die Rollen (Gruppen) mit ihren Standardrechten an."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true",
            help="Rechte genau auf den Stand aus core/roles.py setzen (entfernt Änderungen aus dem Admin).",
        )

    def handle(self, *args, **options):
        for role, names in ROLE_PERMISSIONS.items():
            group, created = Group.objects.get_or_create(name=role)
            if names == ALL_PERMISSIONS:
                permissions = list(Permission.objects.all())
            else:
                permissions = find_permissions(names)

            if options["reset"]:
                group.permissions.set(permissions)
            else:
                group.permissions.add(*permissions)

            from core.models import RoleDefault  # remembered: `migrate` will not add these again
            for permission in permissions:
                RoleDefault.objects.get_or_create(
                    role=role, permission=f"{permission.content_type.app_label}.{permission.codename}")
            state = "angelegt" if created else "aktualisiert"
            self.stdout.write(f"{role}: {state}, {group.permissions.count()} Rechte")
        self.stdout.write(self.style.SUCCESS("Rollen sind eingerichtet."))
