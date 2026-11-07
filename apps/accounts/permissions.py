from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


class RoleRequiredMixin(LoginRequiredMixin):
    """Anonymous -> login page. Wrong role -> 403."""

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role not in self.allowed_roles:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class OwnerRequiredMixin(RoleRequiredMixin):
    allowed_roles = ("OWNER",)


class DriverRequiredMixin(RoleRequiredMixin):
    allowed_roles = ("DRIVER",)


class SuperadminRequiredMixin(RoleRequiredMixin):
    allowed_roles = ("SUPERADMIN",)


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if request.user.role not in roles:
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
