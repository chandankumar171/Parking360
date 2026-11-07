from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.validators import normalize_phone, normalize_plate, validate_phone, validate_plate

from .models import DriverVehicle, OwnerProfile, User


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email", "placeholder": "you@example.com"}),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    error_messages = {
        "invalid_login": "Incorrect email or password.",
        "inactive": "This account has been disabled. Contact support.",
    }

    def clean_username(self):
        return self.cleaned_data["username"].lower()


class SignupForm(forms.Form):
    ROLE_CHOICES = [(User.Role.DRIVER.value, "I'm a driver"), (User.Role.OWNER.value, "I own parking")]

    role = forms.ChoiceField(choices=ROLE_CHOICES, widget=forms.RadioSelect(attrs={"class": "role-input"}))
    full_name = forms.CharField(label="Full name", max_length=120, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    phone = forms.CharField(
        label="Mobile number",
        max_length=20,
        help_text="10-digit Indian mobile number.",
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
    )
    business_name = forms.CharField(
        label="Business or parking name", max_length=150, required=False,
        help_text="Shown to drivers on your public profile.",
    )
    password1 = forms.CharField(
        label="Password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="At least 8 characters. Avoid common passwords.",
    )
    password2 = forms.CharField(
        label="Confirm password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )
    terms = forms.BooleanField(
        required=True, label="I agree to the Terms and Privacy Policy",
        error_messages={"required": "Accept the terms to continue."},
    )

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists. Try logging in.")
        return email

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        validate_phone(phone)
        if User.objects.filter(phone=phone).exists():
            raise ValidationError("This mobile number is already registered.")
        return phone

    def clean(self):
        data = super().clean()
        if data.get("role") == User.Role.OWNER and not (data.get("business_name") or "").strip():
            self.add_error("business_name", "Enter your business or parking name.")
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        elif p1:
            try:
                validate_password(p1, user=User(email=data.get("email", ""), full_name=data.get("full_name", "")))
            except ValidationError as exc:
                self.add_error("password1", exc)
        return data

    @transaction.atomic
    def save(self):
        d = self.cleaned_data
        user = User.objects.create_user(
            email=d["email"], password=d["password1"], full_name=d["full_name"].strip(),
            phone=d["phone"], role=d["role"],
        )
        if user.role == User.Role.OWNER:
            OwnerProfile.objects.create(user=user, business_name=d["business_name"].strip())
        return user


class ProfileForm(forms.ModelForm):
    phone = forms.CharField(label="Mobile number", max_length=20, required=False)

    class Meta:
        model = User
        fields = ["full_name", "phone", "avatar"]
        widgets = {"avatar": forms.FileInput(attrs={"accept": "image/*"})}

    def clean_phone(self):
        raw = self.cleaned_data.get("phone", "")
        if not raw:
            if self.instance.role != User.Role.SUPERADMIN:
                raise ValidationError("Mobile number is required.")
            return None
        phone = normalize_phone(raw)
        validate_phone(phone)
        if User.objects.filter(phone=phone).exclude(pk=self.instance.pk).exists():
            raise ValidationError("This mobile number is already registered.")
        return phone


class OwnerProfileForm(forms.ModelForm):
    class Meta:
        model = OwnerProfile
        fields = ["business_name", "address", "about", "is_public"]
        labels = {"is_public": "Show my profile to drivers"}
        widgets = {"about": forms.Textarea(attrs={"rows": 3})}


class DriverVehicleForm(forms.ModelForm):
    class Meta:
        model = DriverVehicle
        fields = ["category", "plate_number", "brand", "model", "color"]
        labels = {"category": "Vehicle type", "plate_number": "Number plate", "model": "Model"}
        widgets = {"category": forms.Select(attrs={"class": "form-select"})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_plate_number(self):
        plate = normalize_plate(self.cleaned_data["plate_number"])
        validate_plate(plate)
        if self.user and DriverVehicle.objects.filter(user=self.user, plate_number=plate).exists():
            raise ValidationError("You have already saved this vehicle.")
        return plate
