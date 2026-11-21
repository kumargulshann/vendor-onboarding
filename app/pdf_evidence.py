"""
Bank-letter PDF evidence extraction.

A maintained parser (pypdf) reads selectable text from a PDF. The file is
processed transiently: bytes and full extracted text are used only for the
current run and are never persisted. Only the extracted holder, masked account
ending and outcome are stored.

Handles:
  - selectable-text PDFs, including fields on later pages (all pages concatenated)
  - image-only / scanned PDFs -> no extractable text -> treated as unreadable
  - invalid, encrypted, or oversized files -> a clear, recoverable error

OCR and external model integration are intentionally out of scope for now.
"""
from __future__ import annotations
import io
from typing import Optional

from pypdf import PdfReader

# A modest limit suitable for a bank letter (1 MB).
MAX_PDF_BYTES = 1_000_000


class PdfEvidenceError(Exception):
    """Raised when a PDF cannot be turned into usable evidence."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def extract_pdf_text(data: bytes) -> dict:
    """
    Extract text evidence from a PDF.

    Returns {"text": str, "pages": int, "readable": bool}.
    Raises PdfEvidenceError for oversized, invalid, or encrypted files.
    """
    if not data:
        raise PdfEvidenceError("The uploaded file is empty.")
    if len(data) > MAX_PDF_BYTES:
        raise PdfEvidenceError(
            f"The uploaded PDF is too large (max {MAX_PDF_BYTES // 1024} KB). "
            "Please upload a smaller bank letter.")

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        raise PdfEvidenceError(
            "The uploaded file is not a valid PDF. Please choose a valid bank "
            "letter PDF and try again.")

    # Encrypted PDFs: attempt the common empty-password case; otherwise fail
    # clearly so the vendor can re-upload an unprotected letter. pypdf's
    # decrypt() returns 0 when it could NOT decrypt (it does not always raise),
    # so we check the return value to give the right message instead of falling
    # through to a generic "could not be read" error.
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise PdfEvidenceError(
                    "The uploaded PDF is password-protected and cannot be read. "
                    "Please upload an unprotected bank letter PDF.")
        except PdfEvidenceError:
            raise
        except Exception:
            raise PdfEvidenceError(
                "The uploaded PDF is password-protected and cannot be read. "
                "Please upload an unprotected bank letter PDF.")

    pages_text: list[str] = []
    try:
        for page in reader.pages:
            pages_text.append(page.extract_text() or "")
    except Exception:
        raise PdfEvidenceError(
            "The uploaded PDF could not be read. Please try a different bank "
            "letter PDF.")

    text = "\n".join(pages_text)
    return {
        "text": text,
        "pages": len(reader.pages),
        "readable": bool(text.strip()),
    }
