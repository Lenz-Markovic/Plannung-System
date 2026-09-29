"""Give the roles the default permissions that are new since the last `migrate` (core/roles.py)."""

from django.contrib.auth.models import Group, Permission
from django.db.utils import OperationalError, ProgrammingError

from .roles import ALL_PERMISSIONS, ROLE_PERMISSIONS


def grant_new_defaults():
    from .models import RoleDefault

    try:
        existing = {f"{p.content_type.app_label}.{p.codename}": p for p in Permission.objects.select_related("content_type")}
        for role, names in ROLE_PERMISSIONS.items():
            group, _ = Group.objects.get_or_create(name=role)
            names = list(existing) if names == ALL_PERMISSIONS else names
            given = set(RoleDefault.objects.filter(role=role).values_list("permission", flat=True))
            current = {f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.select_related("content_type")}
            for name in names:
                if name in given or name not in existing:
                    continue  # already given once (maybe removed by an admin) / not created yet
                if name not in current:
                    group.permissions.add(existing[name])
                RoleDefault.objects.get_or_create(role=role, permission=name)
    except (OperationalError, ProgrammingError):
        pass  # tables not there yet (first migrate) - the next app's post_migrate tries again
