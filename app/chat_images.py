"""Decode and normalize pictures before storing them as protected attachments."""

import warnings
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000


def normalize_picture(upload):
    raw = upload.read(MAX_IMAGE_BYTES + 1)
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Choose a picture smaller than 5 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as check:
                if check.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                    raise ValueError("Choose a JPG, PNG, WebP, or GIF picture.")
                if check.width * check.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Choose a picture with at most 12 million pixels.")
                check.verify()
            with Image.open(BytesIO(raw)) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail((2048, 2048))
                transparent = "A" in image.getbands() or "transparency" in image.info
                clean = Image.new("RGBA" if transparent else "RGB", image.size)
                clean.paste(image.convert(clean.mode))
                output = BytesIO()
                if transparent:
                    clean.save(output, format="PNG", optimize=True)
                    mime, suffix = "image/png", ".png"
                else:
                    clean.save(output, format="JPEG", quality=88, optimize=True)
                    mime, suffix = "image/jpeg", ".jpg"
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise ValueError(
            "That file could not be read as a picture. Choose a JPG, PNG, WebP, or GIF."
        ) from None
    data = output.getvalue()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Choose a smaller picture.")
    name = secure_filename(upload.filename or "photo").rsplit(".", 1)[0][:140] or "photo"
    return dict(data=data, filename=name + suffix, mime_type=mime)
