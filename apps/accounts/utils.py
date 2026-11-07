from django.urls import reverse


def home_url_for(user):
    name = {
        "SUPERADMIN": "core:superadmin_home",
        "OWNER": "core:owner_home",
    }.get(user.role, "parkings:search")
    return reverse(name)
