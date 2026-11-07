"""Minimal Django storage backend that keeps uploaded images on Cloudinary."""
import os
import uuid

import cloudinary.uploader
from cloudinary.utils import cloudinary_url
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class CloudinaryMediaStorage(Storage):
    is_cloudinary = True

    """Stored name is "<public_id>.<format>" (e.g. avatars/abc_1f2e3d4c.jpg)."""

    def _save(self, name, content):
        root, _ext = os.path.splitext(name.replace("\\", "/"))
        public_id = f"{root}_{uuid.uuid4().hex[:8]}"
        content.seek(0)
        result = cloudinary.uploader.upload(
            content, public_id=public_id, resource_type="image", overwrite=False
        )
        return f"{result['public_id']}.{result.get('format', 'jpg')}"

    def url(self, name, **options):
        public_id, ext = os.path.splitext(name)
        options.setdefault("secure", True)
        options.setdefault("fetch_format", "auto")
        options.setdefault("quality", "auto")
        return cloudinary_url(public_id, **options)[0]

    def exists(self, name):
        return False  # names are always unique

    def delete(self, name):
        public_id, _ext = os.path.splitext(name)
        try:
            cloudinary.uploader.destroy(public_id, invalidate=True)
        except Exception:  # never break a request because a remote delete failed
            pass

    def _open(self, name, mode="rb"):
        raise NotImplementedError("Cloudinary files are served by URL, not opened locally.")

    def size(self, name):
        return 0
