"""Business rules for parking listing, verification and photos. Views stay thin; everything here is atomic."""
import logging

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import SiteSettings
from apps.core.services import audit, notify
from apps.core.validators import validate_image_size

from .models import MAX_PHOTOS, Parking, ParkingPhoto

logger = logging.getLogger(__name__)
S = Parking.Status
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def _locked(parking):
    """Re-read the parking under a row lock so decisions use its real, current status."""
    return Parking.objects.select_for_update().select_related("owner").get(pk=parking.pk)


def _owner_link(parking):
    return reverse("owner:parking_edit", args=[parking.pk, "review"])


# ------------------------------------------------------------------ listing lifecycle
@transaction.atomic
def submit_parking(parking, actor):
    """Owner submits a draft/rejected parking. Returns (ok, [messages])."""
    parking = Parking.objects.select_for_update().get(pk=parking.pk)
    if parking.status not in (S.DRAFT, S.REJECTED):
        return False, ["This parking has already been submitted."]
    issues = parking.blocking_issues()
    if issues:
        return False, issues

    settings_ = SiteSettings.load()
    parking.submitted_at = timezone.now()
    parking.rejection_reason = ""
    parking.is_verified_badge = False
    if settings_.verification_required:
        parking.status = S.PENDING
    else:
        parking.status = S.APPROVED  # goes live straight away, without the Verified badge
    parking.save(update_fields=["status", "submitted_at", "rejection_reason", "is_verified_badge", "updated_at"])

    audit(actor, f"parking.submitted.{parking.status.lower()}", parking, f"{parking.name} submitted ({parking.status}).")
    if parking.status == S.PENDING:
        notify(parking.owner, "Submitted for verification",
               f"{parking.name} will be visible to drivers once approved.", _owner_link(parking))
        for admin in User.objects.filter(role=User.Role.SUPERADMIN, is_active=True):
            notify(admin, "Parking awaiting verification", f"{parking.name} ({parking.city})",
                   reverse("sadmin:parking_detail", args=[parking.pk]))
    else:
        notify(parking.owner, "Your parking is live", f"{parking.name} is now visible to drivers.", _owner_link(parking))
    return True, []


def mark_edited(parking, actor):
    """Owner changed a live parking. Send it back to review only if the platform rule says so."""
    cfg = SiteSettings.load()
    if parking.status == S.APPROVED and cfg.verification_required and cfg.reverify_on_edit:
        parking.status = S.PENDING
        parking.is_verified_badge = False
        parking.save(update_fields=["status", "is_verified_badge", "updated_at"])
        audit(actor, "parking.reverify", parking, f"{parking.name} edited; sent back for verification.")
        notify(parking.owner, "Sent back for verification",
               f"{parking.name} was edited, so it is hidden until it is approved again.", _owner_link(parking))
        return True
    return False


@transaction.atomic
def approve(parking, actor):
    """Superadmin approval: goes live AND receives the Verified badge."""
    parking = _locked(parking)
    if parking.status not in (S.PENDING, S.REJECTED, S.APPROVED):
        raise ValidationError("Only submitted parkings can be approved.")
    parking.status = S.APPROVED
    parking.is_verified_badge = True
    parking.verified_at = timezone.now()
    parking.verified_by = actor
    parking.rejection_reason = ""
    parking.save(update_fields=["status", "is_verified_badge", "verified_at", "verified_by", "rejection_reason", "updated_at"])
    audit(actor, "parking.approved", parking, f"{parking.name} approved and verified.")
    notify(parking.owner, "Parking approved", f"{parking.name} is live with the Verified badge.", _owner_link(parking))


@transaction.atomic
def reject(parking, actor, reason):
    parking = _locked(parking)
    if parking.status not in (S.PENDING, S.APPROVED):
        raise ValidationError("Only pending or live parkings can be rejected.")
    parking.status = S.REJECTED
    parking.is_verified_badge = False
    parking.rejection_reason = reason.strip()[:500]
    parking.save(update_fields=["status", "is_verified_badge", "rejection_reason", "updated_at"])
    audit(actor, "parking.rejected", parking, f"{parking.name} rejected.", reason=parking.rejection_reason)
    notify(parking.owner, "Parking needs changes", parking.rejection_reason, _owner_link(parking))


@transaction.atomic
def suspend(parking, actor, reason=""):
    parking = _locked(parking)
    if parking.status == S.SUSPENDED:
        return
    parking.status = S.SUSPENDED
    parking.rejection_reason = reason.strip()[:500]
    parking.save(update_fields=["status", "rejection_reason", "updated_at"])
    audit(actor, "parking.suspended", parking, f"{parking.name} suspended.", reason=reason)
    notify(parking.owner, "Parking suspended", reason or "Contact support for details.", _owner_link(parking))


@transaction.atomic
def unsuspend(parking, actor):
    parking = _locked(parking)
    if parking.status != S.SUSPENDED:
        return
    parking.status = S.APPROVED
    parking.rejection_reason = ""
    parking.save(update_fields=["status", "rejection_reason", "updated_at"])
    audit(actor, "parking.unsuspended", parking, f"{parking.name} restored.")
    notify(parking.owner, "Parking restored", f"{parking.name} is live again.", _owner_link(parking))


@transaction.atomic
def auto_approve_pending(actor):
    """Called when the superadmin switches verification OFF: everything waiting goes live (no badge)."""
    pending = list(Parking.objects.select_for_update().select_related("owner").filter(status=S.PENDING))
    now = timezone.now()
    for parking in pending:
        parking.status = S.APPROVED
        parking.is_verified_badge = False
        parking.save(update_fields=["status", "is_verified_badge", "updated_at"])
        notify(parking.owner, "Your parking is live", f"{parking.name} no longer needs verification and is now visible.",
               _owner_link(parking))
    audit(actor, "parking.auto_approved", None, f"{len(pending)} pending parking(s) auto-approved.",
          count=len(pending), at=now.isoformat(), ids=[p.pk for p in pending])
    return len(pending)


@transaction.atomic
def delete_parking(parking):
    if parking.has_active_bookings():
        raise ValidationError("This parking has active bookings. Cancel or complete them first.")
    photos = list(parking.photos.all())
    try:
        parking.delete()
    except ProtectedError:
        raise ValidationError("This parking has booking history, so it can't be deleted. Close it for new bookings instead.")
    for photo in photos:  # remove files only after the row is really gone
        photo.image.delete(save=False)


# ------------------------------------------------------------------ photos
def validate_uploaded_image(f):
    """Real image check via Pillow, allowed formats and size."""
    cleaned = forms.ImageField().clean(f)
    validate_image_size(cleaned)
    fmt = getattr(getattr(cleaned, "image", None), "format", None)
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError("Use a JPG, PNG or WebP image.")
    cleaned.seek(0)
    return cleaned


def add_photos(parking, files):
    """Returns (added_count, [error messages])."""
    if not files:
        return 0, ["Choose at least one image."]
    errors, added = [], 0
    with transaction.atomic():
        parking = Parking.objects.select_for_update().get(pk=parking.pk)
        existing = parking.photos.count()
        has_cover = parking.photos.filter(is_cover=True).exists()
        for f in files:
            if existing + added >= MAX_PHOTOS:
                errors.append(f"You can upload up to {MAX_PHOTOS} photos. Extra files were skipped.")
                break
            try:
                validate_uploaded_image(f)
            except ValidationError as exc:
                errors.append(f"{f.name}: {exc.messages[0]}")
                continue
            ParkingPhoto.objects.create(
                parking=parking, image=f, order=existing + added, is_cover=not has_cover and added == 0
            )
            has_cover = True
            added += 1
    return added, errors


@transaction.atomic
def set_cover(photo):
    ParkingPhoto.objects.filter(parking=photo.parking).update(is_cover=False)
    photo.is_cover = True
    photo.save(update_fields=["is_cover"])


@transaction.atomic
def delete_photo(photo):
    parking = photo.parking
    others = parking.photos.exclude(pk=photo.pk)
    if not others.exists() and parking.status in (S.PENDING, S.APPROVED):
        raise ValidationError("A listed parking needs at least one photo. Upload another before deleting this one.")
    was_cover = photo.is_cover
    photo.image.delete(save=False)
    photo.delete()
    if was_cover:
        first = others.order_by("order", "id").first()
        if first:
            first.is_cover = True
            first.save(update_fields=["is_cover"])
