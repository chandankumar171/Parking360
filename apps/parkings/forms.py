from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory

from apps.core.validators import normalize_phone, validate_image_size, validate_phone

from .maps import extract_lat_lng, is_google_maps_url
from .models import AMENITIES, Parking, ParkingVehicleType


class ParkingBasicsForm(forms.ModelForm):
    contact_phone = forms.CharField(label="Contact number", max_length=20, required=False,
                                    help_text="Shown to drivers with a confirmed booking.")

    class Meta:
        model = Parking
        fields = ["name", "description", "address_line", "area", "city", "state", "pincode",
                  "google_maps_url", "latitude", "longitude", "contact_phone"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4}),
                   "latitude": forms.NumberInput(attrs={"step": "any"}),
                   "longitude": forms.NumberInput(attrs={"step": "any"})}
        help_texts = {
            "google_maps_url": "Open your spot in Google Maps, tap Share, and paste the full link. Short links (maps.app.goo.gl) work, but then enter the coordinates below.",
            "latitude": "Filled automatically from the link when possible.",
            "longitude": "Filled automatically from the link when possible.",
        }

    def clean_google_maps_url(self):
        url = self.cleaned_data["google_maps_url"].strip()
        if not is_google_maps_url(url):
            raise ValidationError("Paste a link from Google Maps (google.com/maps or maps.app.goo.gl).")
        return url

    def clean_contact_phone(self):
        raw = self.cleaned_data.get("contact_phone", "")
        if not raw:
            return ""
        phone = normalize_phone(raw)
        validate_phone(phone)
        return phone

    def clean(self):
        data = super().clean()
        lat, lng = data.get("latitude"), data.get("longitude")
        url = data.get("google_maps_url")
        if (lat is None) != (lng is None):
            raise ValidationError("Enter both latitude and longitude, or leave both empty.")
        coords_edited = bool({"latitude", "longitude"} & set(self.changed_data))
        url_edited = "google_maps_url" in self.changed_data
        if url and ((lat is None and lng is None) or (url_edited and not coords_edited)):
            found = extract_lat_lng(url)
            if found:
                data["latitude"], data["longitude"] = found
        if data.get("latitude") is not None:
            if not (-90 <= data["latitude"] <= 90 and -180 <= data["longitude"] <= 180):
                raise ValidationError("Latitude must be between -90 and 90 and longitude between -180 and 180.")
        return data


class VehicleTypeForm(forms.ModelForm):
    class Meta:
        model = ParkingVehicleType
        fields = ["category", "label", "capacity", "slot_minutes", "price_per_slot", "min_slots", "max_slots",
                  "overtime_free_minutes", "overtime_block_minutes", "overtime_charge", "is_active"]
        widgets = {
            "category": forms.Select(attrs={"class": "form-select"}),
            "slot_minutes": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean(self):
        data = super().clean()
        lo, hi = data.get("min_slots"), data.get("max_slots")
        if lo and hi and lo > hi:
            self.add_error("max_slots", "Maximum slots must be at least the minimum.")
        return data


class BaseVehicleFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        for f in self.deleted_forms:
            if f.instance.pk and f.instance.bookings.exists():
                raise ValidationError(
                    f"{f.instance.display_name} has bookings and can't be removed. Turn off 'Accepting this vehicle type' instead.")
        live = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if not live:
            raise ValidationError("Add at least one vehicle type.")
        if not any(f.cleaned_data.get("is_active") for f in live):
            raise ValidationError("Keep at least one vehicle type active.")


VehicleTypeFormSet = inlineformset_factory(
    Parking, ParkingVehicleType, form=VehicleTypeForm, formset=BaseVehicleFormSet,
    extra=0, min_num=1, validate_min=True, max_num=6, can_delete=True,
)


class AmenitiesRulesForm(forms.ModelForm):
    class Meta:
        model = Parking
        fields = [f for f, _l, _i in AMENITIES] + [
            "is_open", "is_24x7", "opening_time", "closing_time", "grace_minutes",
            "allow_cancellation", "cancel_window_minutes", "cancellation_charge_percent", "rules_text",
        ]
        widgets = {
            "opening_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "closing_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "rules_text": forms.Textarea(attrs={"rows": 3}),
            "is_open": forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"}),
            "is_24x7": forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"}),
            "allow_cancellation": forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"}),
            **{f: forms.CheckboxInput(attrs={"class": "amenity-input"}) for f, _l, _i in AMENITIES},
        }
        help_texts = {
            "grace_minutes": "Bookings not checked in this long after the start time are marked as no-show.",
            "cancel_window_minutes": "Drivers can cancel until this many minutes before the booking starts.",
            "cancellation_charge_percent": "Percentage kept when a driver cancels. 0 means a full refund.",
        }

    def clean(self):
        data = super().clean()
        if not data.get("is_24x7") and not (data.get("opening_time") and data.get("closing_time")):
            raise ValidationError("Set both opening and closing time, or mark the parking as open 24 hours.")
        return data

    @property
    def amenity_fields(self):
        return [(self[f], icon) for f, _l, icon in AMENITIES]


class RejectForm(forms.Form):
    reason = forms.CharField(
        label="Reason shown to the owner", min_length=5, max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
