import datetime

from django import forms
from django.db.models import Q

from .models import Employee
from .rules.ordering import STRATEGIES

BREAK_CHOICES = [(30, "30 Min. Mittagspause"), (45, "45 Min. Mittagspause"), (60, "60 Min. Mittagspause"), (0, "ohne Pause")]


def next_working_day(today=None):
    day = (today or datetime.date.today()) + datetime.timedelta(days=1)
    while day.weekday() >= 5:  # Saturday / Sunday
        day += datetime.timedelta(days=1)
    return day


class PlanForm(forms.Form):
    """The dialog "Fahrplan planen" (same fields as in the prototype).

    readings / installations: what is selected. The person list shows readers,
    installers, or - for a plan with both - everybody who can do either.
    """

    employee = forms.ModelChoiceField(label="Ableser", queryset=Employee.objects.filter(can_read=True, active=True),
                                      empty_label="– bitte wählen –")
    date = forms.DateField(label="Tag", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    start = forms.TimeField(label="Beginn", initial=datetime.time(8, 0),
                            widget=forms.TimeInput(attrs={"type": "time", "step": 900}, format="%H:%M"))
    break_minutes = forms.TypedChoiceField(label="Pause", choices=BREAK_CHOICES, coerce=int, initial=30)
    strategy = forms.ChoiceField(label="Reihenfolge", choices=list(STRATEGIES.items()), initial="far")

    def __init__(self, *args, readings=True, installations=False, **kwargs):
        super().__init__(*args, **kwargs)
        if "date" not in self.initial:
            self.fields["date"].initial = next_working_day()
        employee = self.fields["employee"]
        active = Employee.objects.filter(active=True)
        if installations and readings:
            employee.label = "Person (Ableser / Monteur)"
            employee.queryset = active.filter(Q(can_read=True) | Q(can_install=True))
            employee.label_from_instance = lambda e: f"{e} ({'Ableser + Monteur' if e.can_read and e.can_install else 'Monteur' if e.can_install else 'Ableser'})"
        elif installations:
            employee.label, employee.queryset = "Monteur", active.filter(can_install=True)


class DraftSettingsForm(forms.Form):
    """Start time and break can still be changed in the preview."""

    start = PlanForm.base_fields["start"]
    break_minutes = PlanForm.base_fields["break_minutes"]
