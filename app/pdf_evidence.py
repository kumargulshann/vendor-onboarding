"""
Bank-letter PDF evidence extraction.

A maintained parser (pypdf) reads selectable text from a PDF. The file is
processed transiently: bytes and full extracted text are used only for the
current run and are never persisted. Only the extracted holder, masked account
ending and outcome are stored.

Handles:
  - selectable-text PDFs, including fields on later pages (all pages concatenated)
  - scanned onboarding forms -> optional local OCR
  - invalid, encrypted, or oversized files -> a clear, recoverable error

Bank proof continues to use selectable text only.
"""
from __future__ import annotations
import csv
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader

# A modest limit suitable for a bank letter (1 MB).
MAX_PDF_BYTES = 1_000_000
MAX_PDF_PAGES = 20
MAX_PDF_TEXT_CHARS = 64_000
MAX_OCR_PAGES = 4


class PdfEvidenceError(Exception):
    """Raised when a PDF cannot be turned into usable evidence."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def extract_pdf_pages(data: bytes, *, layout: bool = False, document: str = "bank letter") -> list[str]:
    """Read page text within the shared PDF limits."""
    if not data:
        raise PdfEvidenceError("The uploaded file is empty.")
    if len(data) > MAX_PDF_BYTES:
        raise PdfEvidenceError(
            "The uploaded PDF is too large (max 1 MB). "
            f"Please upload a smaller {document}.")

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        raise PdfEvidenceError(
            f"The uploaded file is not a valid PDF. Please choose a valid {document} PDF and try again.")

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
                    f"Please upload an unprotected {document} PDF.")
        except PdfEvidenceError:
            raise
        except Exception:
            raise PdfEvidenceError(
                "The uploaded PDF is password-protected and cannot be read. "
                f"Please upload an unprotected {document} PDF.")

    pages_text: list[str] = []
    text_chars = 0
    try:
        page_count = len(reader.pages)
        if page_count > MAX_PDF_PAGES:
            raise PdfEvidenceError(f"Upload a {document} with at most {MAX_PDF_PAGES} pages.")
        for page in reader.pages:
            page_text = page.extract_text(extraction_mode="layout" if layout else "plain") or ""
            text_chars += len(page_text) + bool(pages_text)
            if text_chars > MAX_PDF_TEXT_CHARS:
                raise PdfEvidenceError(f"The PDF contains too much text. Upload just the {document}.")
            pages_text.append(page_text)
    except PdfEvidenceError:
        raise
    except Exception:
        raise PdfEvidenceError(
            f"The uploaded PDF could not be read. Please try a different {document} PDF.")

    return pages_text


def extract_pdf_text(data: bytes) -> dict:
    """Extract the text used by the bank-letter review."""
    pages = extract_pdf_pages(data, layout=True)
    text = "\n".join(pages)
    return {
        "text": text,
        "pages": len(pages),
        "readable": bool(text.strip()),
    }


def _ocr_layout(tsv: str) -> str:
    """Reconstruct lines and indentation using Tesseract word positions."""
    groups = {}
    for word in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if word["level"] != "5" or not (word["text"] or "").strip():
            continue
        line = (word["block_num"], word["par_num"], word["line_num"])
        groups.setdefault(line, []).append((
            int(word["left"]), int(word["top"]), int(word["width"]),
            int(word["height"]), word["text"].strip(),
        ))
    if not groups:
        return ""

    margin = min(word[0] for words in groups.values() for word in words)
    lines = []
    previous_bottom = None
    for words in groups.values():
        words.sort(key=lambda word: word[0])
        top = min(word[1] for word in words)
        height = max(word[3] for word in words)
        if previous_bottom is not None and top - previous_bottom > 1.4 * height:
            lines.append("")
        line = " " * ((words[0][0] - margin) // 8)
        right = words[0][0]
        for index, (left, _, width, _, value) in enumerate(words):
            if index:
                line += " " * max(1, (left - right) // 8)
            line += value
            right = left + width
        lines.append(line)
        previous_bottom = max(word[1] + word[3] for word in words)
    return "\n".join(lines)


def read_scanned_form(data: bytes, page_count: int) -> list[str]:
    """Read an image-only onboarding form using local OCR when installed."""
    renderer = os.environ.get("ONBOARDING_PDFTOPPM") or shutil.which("pdftoppm")
    ocr = os.environ.get("ONBOARDING_TESSERACT") or shutil.which("tesseract")
    if not renderer or not ocr:
        raise PdfEvidenceError(
            "This PDF has no selectable text and local OCR is unavailable. "
            "Upload a searchable PDF or enter the details manually."
        )
    if not 1 <= page_count <= MAX_OCR_PAGES:
        raise PdfEvidenceError(
            f"OCR supports up to {MAX_OCR_PAGES} pages. Upload a shorter scan or enter the details manually."
        )

    pages = []
    total_chars = 0
    try:
        with tempfile.TemporaryDirectory(prefix="onboarding-ocr-") as directory:
            root = Path(directory)
            document = root / "form.pdf"
            document.write_bytes(data)
            subprocess.run(
                [renderer, "-f", "1", "-l", str(page_count), "-scale-to", "2000",
                 "-png", str(document), str(root / "page")],
                check=True, capture_output=True, timeout=10,
            )
            images = sorted(root.glob("page-*.png"), key=lambda item: int(item.stem.rsplit("-", 1)[1]))
            if len(images) != page_count:
                raise PdfEvidenceError("The scan could not be rendered. Try a searchable PDF.")
            for image in images:
                result = subprocess.run(
                    [ocr, str(image), "stdout", "-l", "eng", "--psm", "6",
                     "-c", "tessedit_create_tsv=1"],
                    check=True, capture_output=True, encoding="utf-8", errors="replace", timeout=8,
                )
                page = _ocr_layout(result.stdout)
                total_chars += len(page) + bool(pages)
                if total_chars > MAX_PDF_TEXT_CHARS:
                    raise PdfEvidenceError("The scan contains too much text. Upload just the onboarding form.")
                pages.append(page)
    except (OSError, subprocess.SubprocessError, KeyError, ValueError, csv.Error) as error:
        raise PdfEvidenceError("Local OCR could not finish. Upload a searchable PDF or enter the details manually.") from error
    if not any(page.strip() for page in pages):
        raise PdfEvidenceError("No text could be read from the scan. Upload a clearer copy or enter the details manually.")
    return pages
