"""Bounded, private image files owned by a session, outside model transcripts."""

import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import tempfile

from PIL import Image

from .storage import Storage

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
IMAGE_FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
                 "WEBP": ("webp", "image/webp"), "GIF": ("gif", "image/gif"),
                 "BMP": ("bmp", "image/bmp"), "TIFF": ("tiff", "image/tiff")}


def image_directory(session_id: str) -> Path:
    return Storage.BASE_DIR / "images" / hashlib.sha256(session_id.encode()).hexdigest()


def image_path(image: dict) -> Path | None:
    """Resolve only Codesm cache references, never paths supplied by a model."""
    name = image.get("path", "")
    if not isinstance(name, str) or not re.fullmatch(r"[a-f0-9]{64}/[a-f0-9]{64}\.(png|jpg|webp|gif|bmp|tiff)", name):
        return None
    root = (Storage.BASE_DIR / "images").resolve()
    path = (root / name).resolve()
    return path if path.is_relative_to(root) else None


def save_image(data: bytes, session_id: str) -> dict:
    """Validate the actual image before retaining its original bytes."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the 20 MiB preview limit or is empty")
    try:
        with Image.open(io.BytesIO(data), formats=list(IMAGE_FORMATS)) as image:
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("Image exceeds the 25 megapixel preview limit")
            extension, media_type = IMAGE_FORMATS[image.format]
            image.load()
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError("Image data is damaged or unsupported") from error
    directory = image_directory(session_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{hashlib.sha256(data).hexdigest()}.{extension}"
    if not path.exists():
        fd, temporary = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return {"path": f"{directory.name}/{path.name}", "width": width, "height": height,
            "media_type": media_type}


def copy_images(messages: list[dict], session_id: str):
    """Give a fork its own files so deleting its parent doesn't remove its plots."""
    directory = image_directory(session_id)
    for message in messages:
        image = message.get("image")
        if not isinstance(image, dict):
            continue
        source = image_path(image)
        if source is not None:
            target = directory / source.name
            if source.is_file():
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(source, target)
                target.chmod(0o600)
            image["path"] = f"{directory.name}/{target.name}"


def delete_images(session_id: str):
    directory = image_directory(session_id)
    if directory.exists():
        shutil.rmtree(directory)
