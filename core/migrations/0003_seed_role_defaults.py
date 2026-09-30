"""Remember the defaults that existing roles already had BEFORE RoleDefault existed.

Without this, the first `migrate` after the update treats every default as new and gives
back permissions an admin had taken away on purpose. Only permissions that are really new
since then (listed below) are left for core/role_sync.py to grant.
"""

from django.db import migrations

# Defaults introduced together with or after RoleDefault - these are still granted automatically.
NEW_SINCE_ROLEDEFAULT = {
    "journal.view_note", "journal.add_note", "journal.change_note", "journal.delete_note",
    "journal.view_activity", "journal.add_activity", "journal.change_activity", "journal.delete_activity",
    "buildings.view_costs", "buildings.view_articleprice", "buildings.add_articleprice",
    "buildings.change_articleprice", "buildings.delete_articleprice",
    "planning.view_visit", "planning.add_visit", "planning.change_visit", "planning.delete_visit",
    "planning.process_visit",
    "core.view_roledefault", "core.add_roledefault", "core.change_roledefault", "core.delete_roledefault",
}


ALL_PERMISSIONS = "__all__"
# frozen copy of core/roles.py at the time of this migration (the live file changes later)
ROLE_PERMISSIONS = {   'Ableser/Monteur': [   'buildings.propose_status',
                           'planning.add_field_note',
                           'planning.mark_stop_done',
                           'planning.view_own_tours'],
    'Admin': '__all__',
    'Disposition': [   'buildings.add_propertymanager',
                       'buildings.assign_employee',
                       'buildings.change_building',
                       'buildings.change_installationorder',
                       'buildings.change_propertymanager',
                       'buildings.set_status_open_rework',
                       'buildings.view_building',
                       'buildings.view_devicecategory',
                       'buildings.view_installationorder',
                       'buildings.view_installationorderitem',
                       'buildings.view_propertymanager',
                       'conflicts.acknowledge_conflict',
                       'conflicts.view_conflict',
                       'documents.add_costdocumentreceipt',
                       'documents.change_costdocumentreceipt',
                       'documents.view_costdocumentreceipt',
                       'documents.view_coversheet',
                       'journal.add_note',
                       'journal.change_note',
                       'journal.view_activity',
                       'journal.view_note',
                       'planning.add_absence',
                       'planning.add_tour',
                       'planning.add_tourstop',
                       'planning.change_absence',
                       'planning.change_tour',
                       'planning.change_tourstop',
                       'planning.confirm_tour',
                       'planning.delete_absence',
                       'planning.delete_tour',
                       'planning.delete_tourstop',
                       'planning.view_absence',
                       'planning.view_employee',
                       'planning.view_reports',
                       'planning.view_tour',
                       'planning.view_tourstop'],
    'Leitung': [   'buildings.view_building',
                   'buildings.view_devicecategory',
                   'buildings.view_installationorder',
                   'buildings.view_installationorderitem',
                   'buildings.view_propertymanager',
                   'conflicts.view_conflict',
                   'documents.view_costdocumentreceipt',
                   'documents.view_coversheet',
                   'journal.view_activity',
                   'journal.view_note',
                   'planning.view_absence',
                   'planning.view_employee',
                   'planning.view_reports',
                   'planning.view_tour',
                   'planning.view_tourstop'],
    'Sachbearbeitung': [   'buildings.add_propertymanager',
                           'buildings.change_building',
                           'buildings.change_propertymanager',
                           'buildings.release_building',
                           'buildings.set_status_open_rework',
                           'buildings.view_building',
                           'buildings.view_devicecategory',
                           'buildings.view_installationorder',
                           'buildings.view_installationorderitem',
                           'buildings.view_propertymanager',
                           'conflicts.view_conflict',
                           'documents.add_costdocumentreceipt',
                           'documents.change_costdocumentreceipt',
                           'documents.view_costdocumentreceipt',
                           'documents.view_coversheet',
                           'journal.add_note',
                           'journal.change_note',
                           'journal.view_activity',
                           'journal.view_note',
                           'planning.view_absence',
                           'planning.view_employee',
                           'planning.view_reports',
                           'planning.view_tour',
                           'planning.view_tourstop']}


def seed(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    RoleDefault = apps.get_model("core", "RoleDefault")
    existing = [f"{app}.{code}" for app, code in Permission.objects.values_list("content_type__app_label", "codename")]
    for role, names in ROLE_PERMISSIONS.items():
        if not Group.objects.filter(name=role).exists():
            continue  # a fresh database: the roles are created after migrate with all defaults
        names = existing if names == ALL_PERMISSIONS else names
        for name in names:
            if name not in NEW_SINCE_ROLEDEFAULT:
                RoleDefault.objects.get_or_create(role=role, permission=name)


class Migration(migrations.Migration):

    dependencies = [("core", "0002_roledefault")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
