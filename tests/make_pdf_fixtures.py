"""
Generate small synthetic bank-letter PDF fixtures for Day 2 verification.

Run from the zamp/ folder:
    python3 -m tests.make_pdf_fixtures

Outputs (in data/pdf/):
  pdf_two_page_match.pdf  - valid 2-page letter; holder+account on page 2
  pdf_account_mismatch.pdf - correct holder, different account
  pdf_image_only.pdf      - valid PDF with no selectable text (image-only)
  invalid.pdf             - not a valid PDF (corrupt bytes)
"""
from __future__ import annotations
import pathlib

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "pdf"
OUT.mkdir(parents=True, exist_ok=True)

HOLDER = "Acme Manufacturing Pvt Ltd"
ACCOUNT = "1234567890"


def two_page_match(path):
    c = canvas.Canvas(str(path), pagesize=A4)
    # page 1: header only
    c.drawString(72, 780, "BANK OF TEST")
    c.drawString(72, 760, "Statement of Account")
    c.showPage()
    # page 2: the evidence
    c.drawString(72, 780, "Account holder: " + HOLDER)
    c.drawString(72, 760, "Account number: " + ACCOUNT)
    c.showPage()
    c.save()


def account_mismatch(path):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(72, 780, "Account holder: " + HOLDER)
    c.drawString(72, 760, "Account number: 9999999999")
    c.showPage()
    c.save()


def image_only(path):
    # A valid PDF with a drawn shape but NO text -> no selectable text.
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFillColorRGB(0.2, 0.4, 0.7)
    c.rect(120, 400, 300, 120, fill=1, stroke=0)
    c.showPage()
    c.save()


def invalid(path):
    path.write_bytes(b"this is not a real PDF file\n" * 20)


def main():
    two_page_match(OUT / "pdf_two_page_match.pdf")
    account_mismatch(OUT / "pdf_account_mismatch.pdf")
    image_only(OUT / "pdf_image_only.pdf")
    invalid(OUT / "invalid.pdf")
    for p in sorted(OUT.iterdir()):
        print(f"{p.name:28s} {p.stat().st_size} bytes")


if __name__ == "__main__":
    main()
