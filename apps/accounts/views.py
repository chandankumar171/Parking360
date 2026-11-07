from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.views.generic import FormView
from django_ratelimit.decorators import ratelimit

from .forms import DriverVehicleForm, LoginForm, OwnerProfileForm, ProfileForm, SignupForm
from .models import DriverVehicle, OwnerProfile, User
from .permissions import role_required
from .utils import home_url_for


@method_decorator(ratelimit(key="ip", rate="30/m", method="POST", block=True), name="dispatch")
@method_decorator(ratelimit(key="post:username", rate="10/m", method="POST", block=True), name="dispatch")
class LoginView(auth_views.LoginView):
    template_name = "auth/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        return home_url_for(self.request.user)


@method_decorator(ratelimit(key="ip", rate="20/h", method="POST", block=True), name="dispatch")
class SignupView(FormView):
    template_name = "auth/signup.html"
    form_class = SignupForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(home_url_for(request.user))
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        role = self.request.GET.get("role", "").upper()
        return {"role": role if role in (User.Role.DRIVER, User.Role.OWNER) else User.Role.DRIVER}

    def form_valid(self, form):
        user = form.save()
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(self.request, f"Welcome to ParkShare, {user.get_short_name()}!")
        return redirect(home_url_for(user))


@method_decorator(ratelimit(key="ip", rate="10/h", method="POST", block=True), name="dispatch")
class PasswordResetView(auth_views.PasswordResetView):
    template_name = "auth/password_reset_form.html"
    email_template_name = "emails/password_reset_email.txt"
    subject_template_name = "emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "auth/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        messages.success(self.request, "Password updated.")
        return super().form_valid(form)


@login_required
def profile(request):
    user = request.user
    owner_profile = None
    if user.role == User.Role.OWNER:
        owner_profile, _ = OwnerProfile.objects.get_or_create(
            user=user, defaults={"business_name": user.full_name}
        )
    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=user)
        owner_form = OwnerProfileForm(request.POST, instance=owner_profile) if owner_profile else None
        if form.is_valid() and (owner_form is None or owner_form.is_valid()):
            form.save()
            if owner_form:
                owner_form.save()
            messages.success(request, "Profile saved.")
            return redirect("accounts:profile")
        messages.error(request, "Please fix the errors below.")
    else:
        form = ProfileForm(instance=user)
        owner_form = OwnerProfileForm(instance=owner_profile) if owner_profile else None
    ctx = {"form": form, "owner_form": owner_form}
    if user.role == User.Role.DRIVER:
        from apps.accounts.models import PointLog

        ctx["point_logs"] = PointLog.objects.filter(user=user)[:20]
    return render(request, "accounts/profile.html", ctx)


@role_required("DRIVER")
def vehicles(request):
    if request.method == "POST":
        form = DriverVehicleForm(request.POST, user=request.user)
        if form.is_valid():
            vehicle = form.save(commit=False)
            vehicle.user = request.user
            vehicle.save()
            messages.success(request, "Vehicle saved.")
            return redirect("accounts:vehicles")
    else:
        form = DriverVehicleForm(user=request.user)
    return render(request, "accounts/vehicles.html", {"form": form, "vehicles": request.user.vehicles.all()})


@role_required("DRIVER")
@require_POST
def vehicle_delete(request, pk):
    get_object_or_404(DriverVehicle, pk=pk, user=request.user).delete()
    messages.success(request, "Vehicle removed.")
    return redirect("accounts:vehicles")
