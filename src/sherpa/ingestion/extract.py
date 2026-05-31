"""PDF text extraction with OCR fallback (handoff §3, §9).

PyMuPDF pulls the text layer fast. Scanned / image-only pages have no extractable
text, so we detect those and route them through Tesseract OCR — otherwise they'd
silently produce empty chunks.
"""

from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF

from sherpa.config import settings


class PermanentExtractError(Exception):
    """Unrecoverable: corrupt, not a PDF, or password-protected. Do NOT retry."""


@dataclass
class PageText:
    page: int  # 1-based page number, for display/linking
    text: str


# Render scale for OCR: 2x ~= 144 DPI, a good accuracy/speed tradeoff.
_OCR_ZOOM = 2.0
_MIN_TEXT_CHARS = 10  # below this, treat the page as "no real text" and try OCR


def _ocr_page(page: fitz.Page) -> str:
    # Imported lazily so the API image (no Tesseract) never needs these.
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(matrix=fitz.Matrix(_OCR_ZOOM, _OCR_ZOOM))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return pytesseract.image_to_string(img)


def extract_pages(pdf_bytes: bytes) -> list[PageText]:
    """Extract text per page, OCR-ing pages that have no usable text layer.

    Raises PermanentExtractError for files that can never succeed (so the worker
    sends them straight to the dead-letter path instead of retrying).
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # corrupt / not a PDF
        raise PermanentExtractError(f"cannot open PDF: {exc}") from exc

    try:
        if doc.needs_pass:
            raise PermanentExtractError("PDF is password-protected")

        pages: list[PageText] = []
        for i, page in enumerate(doc):
            text = page.get_text("text").strip()
            if len(text) < _MIN_TEXT_CHARS and settings.ocr_enabled:
                text = _ocr_page(page).strip()
            pages.append(PageText(page=i + 1, text=text))
        return pages
    finally:
        doc.close()
