from django import forms
from django.core.exceptions import ValidationError

from .models import SiteSettings


class PlatformSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = [
            "verification_required", "reverify_on_edit", "platform_fee_percent",
            "default_grace_minutes", "default_cancel_window_minutes", "support_email",
            "points_start", "points_satisfied", "points_unsatisfied", "points_no_show",
            "points_late_cancel", "points_no_checkout_verify",
        ]
        widgets = {
            "verification_required": forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"}),
            "reverify_on_edit": forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"}),
        }
        labels = {
            "verification_required": "Parkings need superadmin verification before drivers see them",
            "reverify_on_edit": "Send a live parking back to review when the owner edits it",
            "platform_fee_percent": "Platform fee (%)",
            "default_grace_minutes": "Default no-show grace (minutes)",
            "default_cancel_window_minutes": "Default cancel window (minutes)",
            "points_start": "Starting points",
            "points_satisfied": "Points for a satisfied checkout",
            "points_unsatisfied": "Points for an unsatisfied checkout",
            "points_no_show": "Points for a no-show",
            "points_late_cancel": "Points for a late cancellation",
            "points_no_checkout_verify": "Points when checkout is never verified",
        }
        help_texts = {"platform_fee_percent": "Taken from every booking and overtime payment. 0 is allowed."}

    def clean_platform_fee_percent(self):
        fee = self.cleaned_data["platform_fee_percent"]
        if not 0 <= fee <= 100:
            raise ValidationError("Enter a value between 0 and 100.")
        return fee

    def clean(self):
        data = super().clean()
        if (data.get("points_satisfied") or 0) < 0:
            self.add_error("points_satisfied", "Use zero or a positive number.")
        for f in ("points_unsatisfied", "points_no_show", "points_late_cancel", "points_no_checkout_verify"):
            if (data.get(f) or 0) > 0:
                self.add_error(f, "Penalties must be zero or negative (for example -10).")
        return data
