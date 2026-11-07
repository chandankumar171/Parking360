def image_url(fieldfile, width=None, height=None):
    """Return an image URL. On Cloudinary this requests a resized, auto-format version."""
    if not fieldfile:
        return ""
    try:
        storage = fieldfile.storage
        if getattr(storage, "is_cloudinary", False) and (width or height):
            opts = {"crop": "fill"}
            if width:
                opts["width"] = width
            if height:
                opts["height"] = height
            return storage.url(fieldfile.name, **opts)
        return fieldfile.url
    except Exception:
        return ""


def indian_number(n):
    """12345678 -> '1,23,45,678'"""
    s = str(int(n))
    neg = s.startswith("-")
    s = s.lstrip("-")
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if neg else "") + s


def qr_svg(text):
    """Inline-able SVG QR code for a short string."""
    import io

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode()
    return svg[svg.index("<svg"):]
