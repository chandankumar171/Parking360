from datetime import datetime, timedelta

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.validators import normalize_plate, validate_plate
from apps.parkings.models import ParkingVehicleType

from .availability import window_problem


class QuoteForm(forms.Form):
    vehicle_type = forms.ModelChoiceField(queryset=ParkingVehicleType.objects.none(), empty_label=None)
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time", "step": 900}, format="%H:%M"))
    slots = forms.IntegerField(min_value=1, max_value=200, initial=1)

    start_at = None
    end_at = None

    def __init__(self, *args, parking, **kwargs):
        super().__init__(*args, **kwargs)
        self.parking = parking
        self.fields["vehicle_type"].queryset = parking.vehicle_types.filter(is_active=True)

    def clean(self):
        data = super().clean()
        vt, day, at, slots = (data.get(k) for k in ("vehicle_type", "date", "time", "slots"))
        if not all([vt, day, at, slots]):
            return data
        if not vt.min_slots <= slots <= vt.max_slots:
            self.add_error("slots", f"Choose between {vt.min_slots} and {vt.max_slots} slots for {vt.display_name}.")
            return data
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(day, at.replace(second=0, microsecond=0)), tz)
        end = start + timedelta(minutes=vt.slot_minutes * slots)
        problem = window_problem(self.parking, start, end)
        if problem:
            raise ValidationError(problem)
        self.start_at, self.end_at = start, end
        return data


class BookingForm(QuoteForm):
    vehicle_number_plate = forms.CharField(
        label="Vehicle number plate", max_length=15,
        widget=forms.TextInput(attrs={"placeholder": "OD02AB1234", "autocapitalize": "characters", "autocomplete": "off"}),
    )
    vehicle_brand_model = forms.CharField(
        label="Brand and model", min_length=2, max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "Tata Nexon"}),
    )
    terms = forms.BooleanField(error_messages={"required": "Please accept the booking terms."})

    def clean_vehicle_number_plate(self):
        plate = normalize_plate(self.cleaned_data["vehicle_number_plate"])
        validate_plate(plate)
        return plate


class ReasonForm(forms.Form):
    reason = forms.CharField(min_length=3, max_length=255, widget=forms.Textarea(attrs={"rows": 2}))


class VerdictForm(forms.Form):
    satisfied = forms.TypedChoiceField(
        choices=[("yes", "Satisfied"), ("no", "Not satisfied")], coerce=lambda v: v == "yes", widget=forms.RadioSelect)
    comment = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 2}))
