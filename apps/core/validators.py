import re

from django.core.exceptions import ValidationError

PHONE_RE = re.compile(r"^[6-9]\d{9}$")
# Standard Indian plates (e.g. OD02AB1234, DL1CAB1234) and BH-series (e.g. 22BH1234AA)
PLATE_RE = re.compile(r"^([A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}|\d{2}BH\d{4}[A-Z]{1,2})$")
MAX_IMAGE_MB = 5


def normalize_phone(value):
    v = re.sub(r"[\s\-()]", "", str(value or ""))
    if v.startswith("+91"):
        v = v[3:]
    elif v.startswith("91") and len(v) == 12:
        v = v[2:]
    elif v.startswith("0") and len(v) == 11:
        v = v[1:]
    return v


def validate_phone(value):
    if not PHONE_RE.match(value or ""):
        raise ValidationError("Enter a valid 10-digit Indian mobile number.")


def normalize_plate(value):
    return re.sub(r"[\s\-]", "", str(value or "")).upper()


def validate_plate(value):
    if not PLATE_RE.match(value or ""):
        raise ValidationError("Enter a valid vehicle number, for example OD02AB1234.")


def validate_image_size(f):
    if f.size > MAX_IMAGE_MB * 1024 * 1024:
        raise ValidationError(f"Image must be smaller than {MAX_IMAGE_MB} MB.")
