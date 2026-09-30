"""After `migrate`, NEW default permissions reach the roles by themselves - removed ones stay removed."""

import pytest
from django.contrib.auth.models import Group, Permission

from core import roles
from core.models import RoleDefault
from core.role_sync import grant_new_defaults

pytestmark = pytest.mark.django_db


def test_new_defaults_are_added_but_an_admins_removal_stays():
    grant_new_defaults()
    group = Group.objects.get(name=roles.DISPATCHER)
    activity = Permission.objects.get(codename="view_activity")
    assert group.permissions.filter(pk=activity.pk).exists()  # new permission: given automatically

    group.permissions.remove(activity)  # an admin takes it away on purpose
    grant_new_defaults()
    assert not group.permissions.filter(pk=activity.pk).exists()

    RoleDefault.objects.filter(role=roles.DISPATCHER, permission="journal.view_activity").delete()  # "new" again
    grant_new_defaults()
    assert group.permissions.filter(pk=activity.pk).exists()


def test_mein_tag_is_not_a_disposition_default():
    grant_new_defaults()
    assert not Group.objects.get(name=roles.DISPATCHER).permissions.filter(codename="view_own_tours").exists()
    assert Group.objects.get(name=roles.READER).permissions.filter(codename="view_own_tours").exists()


def test_setup_roles_does_not_give_back_removed_defaults():
    import io
    from django.core.management import call_command
    grant_new_defaults()
    group = Group.objects.get(name=roles.DISPATCHER)
    delete = Permission.objects.get(codename="delete_tour")
    group.permissions.remove(delete)
    call_command("setup_roles", stdout=io.StringIO())
    assert not group.permissions.filter(pk=delete.pk).exists()
    call_command("setup_roles", "--reset", stdout=io.StringIO())
    assert group.permissions.filter(pk=delete.pk).exists()
