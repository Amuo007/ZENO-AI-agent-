"""Getting images and PDFs ready for a model to look at (a Bit's view_file)."""

import base64
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
# Claude reads images best up to about this many pixels on the long side; bigger ones cost more
# tokens without showing more.
MAX_IMAGE_SIDE = 1568
MAX_PDF_BYTES = 18 * 1024 * 1024  # base64 grows it by a third; requests are capped at 32 MB
MAX_PDF_TEXT_CHARS = 40000


@dataclass
class Attachment:
    kind: str  # "image" or "pdf"
    media_type: str
    data: str  # base64
    name: str


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


def is_pdf(path: Path) -> bool:
    return path.suffix.lower() == ".pdf"


def image_attachment(path: Path) -> tuple[Attachment, str]:
    """The image, scaled down and re-encoded for a model, and a line describing it."""
    with Image.open(path) as original:
        width, height = original.size
        image = ImageOps.exif_transpose(original)
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buffer = io.BytesIO()
        transparent = image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        )
        if transparent:
            image.convert("RGBA").save(buffer, "PNG", optimize=True)
            media_type = "image/png"
        else:
            image.convert("RGB").save(buffer, "JPEG", quality=85)
            media_type = "image/jpeg"
    data = base64.b64encode(buffer.getvalue()).decode()
    return Attachment("image", media_type, data, path.name), f"{width}×{height} pixels"


def pdf_attachment(path: Path) -> Attachment:
    return Attachment("pdf", "application/pdf", base64.b64encode(path.read_bytes()).decode(), path.name)


def pdf_text(path: Path) -> tuple[str, int]:
    """A PDF's text layer and its page count, for models that can't read PDFs themselves."""
    from pypdf import PdfReader

    reader = PdfReader(path)
    parts = []
    size = 0
    for number, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        parts.append(f"--- Page {number} ---\n{text or '(no text on this page)'}")
        size += len(parts[-1])
        if size > MAX_PDF_TEXT_CHARS:
            parts.append(f"… [stopped at page {number} of {len(reader.pages)}]")
            break
    return "\n\n".join(parts), len(reader.pages)
