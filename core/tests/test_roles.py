"""The default permissions must match the role table in spec section 5."""

import pytest
from django.contrib.auth.models import Group, User
from django.core.management import call_command

from core import roles


@pytest.fixture
def user_with_role(db):
    call_command("setup_roles")

    def make(role):
        user = User.objects.create_user(username=role.replace("/", "_"), password="x")
        user.groups.add(Group.objects.get(name=role))
        return User.objects.get(pk=user.pk)  # fresh object, no permission cache

    return make


def test_all_five_roles_exist(user_with_role):
    assert set(Group.objects.values_list("name", flat=True)) == set(roles.ALL_ROLES)


@pytest.mark.parametrize(
    "role, permission, allowed",
    [
        # Fahrpläne erstellen/verschieben/löschen: Admin + Disposition
        (roles.DISPATCHER, "planning.confirm_tour", True),
        (roles.PROCESSING, "planning.confirm_tour", False),
        (roles.MANAGEMENT, "planning.change_tour", False),
        # Status "freigegeben": Admin + Sachbearbeitung only
        (roles.PROCESSING, "buildings.release_building", True),
        (roles.DISPATCHER, "buildings.release_building", False),
        (roles.READER, "buildings.release_building", False),
        # Status offen/Nacharbeit: readers may only propose
        (roles.READER, "buildings.set_status_open_rework", False),
        (roles.READER, "buildings.propose_status", True),
        # Ableser sees only own appointments, not the whole list
        (roles.READER, "buildings.view_building", False),
        (roles.READER, "planning.view_own_tours", True),
        (roles.READER, "planning.mark_stop_done", True),
        (roles.PROCESSING, "planning.mark_stop_done", False),
        # Unterlagen-Eingang: Sachbearbeitung yes, Leitung no
        (roles.PROCESSING, "documents.add_costdocumentreceipt", True),
        (roles.MANAGEMENT, "documents.add_costdocumentreceipt", False),
        # Leitung: view + reports, but no changes
        (roles.MANAGEMENT, "buildings.view_building", True),
        (roles.MANAGEMENT, "planning.view_reports", True),
        (roles.MANAGEMENT, "buildings.change_building", False),
        # Admin: everything, including user management
        (roles.ADMIN, "auth.change_user", True),
        (roles.DISPATCHER, "auth.change_user", False),
    ],
)
def test_role_permissions(user_with_role, role, permission, allowed):
    assert user_with_role(role).has_perm(permission) is allowed


def test_setup_roles_keeps_permissions_added_in_admin(user_with_role):
    group = Group.objects.get(name=roles.MANAGEMENT)
    extra = Group.objects.get(name=roles.ADMIN).permissions.get(codename="change_building")
    group.permissions.add(extra)

    call_command("setup_roles")
    assert group.permissions.filter(pk=extra.pk).exists()

    call_command("setup_roles", "--reset")
    assert not group.permissions.filter(pk=extra.pk).exists()
