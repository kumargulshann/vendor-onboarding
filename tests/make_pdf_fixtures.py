"""
Generate synthetic bank-letter and onboarding PDF fixtures.

Run from the project folder:
    python3 -m tests.make_pdf_fixtures
    python3 -m tests.make_pdf_fixtures --scan   # additionally requires pypdfium2

Outputs (in data/pdf/):
  pdf_two_page_match.pdf  - valid 2-page letter; holder+account on page 2
  pdf_account_mismatch.pdf - correct holder, different account
  pdf_image_only.pdf      - valid PDF with no selectable text (image-only)
  invalid.pdf             - not a valid PDF (corrupt bytes)

The edge-case bank packet is generated in output/pdf/.
"""
from __future__ import annotations
import argparse
import io
import pathlib

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from PIL import Image, ImageDraw

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "pdf"
OUT.mkdir(parents=True, exist_ok=True)
DEMO_PDFS = OUT.parents[1] / "output" / "pdf"

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
    # Burn the letter into one raster image so it behaves like a scanned copy.
    image = Image.new("RGB", (250, 150), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 249, 27), fill="#163654")
    draw.text((12, 9), "BANK OF TEST  /  SAMPLE LETTER", fill="white")
    draw.text((12, 42), "Synthetic bank letter for application testing", fill="black")
    draw.text((12, 65), "Account holder: " + HOLDER, fill="black")
    draw.text((12, 84), "Account number: " + ACCOUNT, fill="black")
    draw.text((12, 120), "Scanned copy", fill="#334155")
    image = image.resize((750, 450), Image.Resampling.NEAREST)
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawImage(ImageReader(image), 48, 473, width=500, height=300)
    c.showPage()
    c.save()


def invalid(path):
    path.write_bytes(b"this is not a real PDF file\n" * 20)


def onboarding_form(path, *, conflicting_account=False):
    """Build a fictional intake form patterned on the supplied two-page layout."""
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle("Synthetic Vendor Onboarding Form")
    c.setAuthor("Vendor Onboarding Demo")
    c.setSubject("Fictional application test data")

    def page_header(number):
        c.setFillColor(colors.HexColor("#142c49"))
        c.setFont("Helvetica-Bold", 18)
        c.drawString(44, 782, "Vendor onboarding details form")
        c.setFillColor(colors.HexColor("#475569"))
        c.setFont("Helvetica", 9)
        c.drawString(44, 761, "SYNTHETIC TEST DATA / ALL DETAILS ARE FICTIONAL")
        c.drawRightString(551, 43, f"Page {number} of 2")

    def draw_rows(rows):
        table = Table(rows, colWidths=(219, 288), rowHeights=24)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbe7f4")),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#142c49")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#a4b5c8")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ]))
        _, height = table.wrap(507, 650)
        table.drawOn(c, 44, 738 - height)
        return 738 - height

    page_header(1)
    rows = [
        ("Supplier details", "Declared value"),
        ("Full Name of Vendor/ Supplier :", "Juniper Office Systems Pvt Ltd"),
        ("Registered Legal Name :", "Juniper Office Systems Pvt Ltd"),
        ("Vendor/Supplier Address :", "18 Sample Lane, Bengaluru, Karnataka"),
        ("Postal Code/City:", "560999"),
        ("Country:", "India"),
        ("Mobile No :", "Not Applicable"),
        ("E-mail ID :", "" if conflicting_account else "procurement@juniper.example"),
        ("Contact Person :", "Sample Procurement Desk"),
        ("Bank Details", "Declared value"),
        ("Bank Name :", "Fictional Demonstration Bank"),
        ("Bank Account Holder Name :", "Juniper Office Systems Pvt Ltd"),
        ("Bank Acocunt No :", "001234567890"),
        ("IFSC Code of branch :", "DEMO0001234"),
        ("Nature of account (SB/CC/CA) :", "Current Account"),
        ("Branch :", "Sample Branch"),
        ("GST REGISTRATION NUMBER", "29ABCDE1234F1Z5"),
        ("PAN no :", "ABCDE1234F"),
        ("Credit Days", "30 days"),
    ]
    if conflicting_account:
        rows = [row for row in rows if row[0] not in ("Registered Legal Name :", "Bank Account Holder Name :")]
    draw_rows(rows)
    c.showPage()

    page_header(2)
    rows = [
        ("Certifications and declaration", "Declared value"),
        ("Category of Enterprises (MSME)", "Not Applicable"),
        ("ISO or other certifications", "Not Applicable"),
    ]
    if conflicting_account:
        rows.append(("Bank Account Number :", "001234569999"))
    bottom = draw_rows(rows)
    c.setFont("Helvetica", 10)
    c.drawString(44, bottom - 42, "I declare that the details in this synthetic sample are for application testing.")
    c.drawString(44, bottom - 89, "Signature: Sample Vendor Representative")
    c.drawRightString(551, bottom - 89, "Date: 23/11/2026")
    c.save()


def supplier_registration_form(path):
    """Build a second synthetic onboarding layout with stacked and inline fields."""
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle("Supplier Registration Form - Synthetic Sample")
    c.setAuthor("Vendor Onboarding Demo")
    ink = colors.HexColor("#19354b")
    accent = colors.HexColor("#0c615d")
    muted = colors.HexColor("#4c6372")

    def write(x, y, value, size=11, bold=False, color=ink):
        c.setFillColor(color)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x, y, value)

    def header(section, number):
        c.setFillColor(accent)
        c.rect(0, 830, A4[0], 12, fill=1, stroke=0)
        write(44, 781, "Supplier Registration Form", 22, True)
        write(44, 753, section, 14, True, accent)
        write(44, 730, "SYNTHETIC SAMPLE  /  ALL DETAILS ARE FICTIONAL", 9, True, muted)
        c.setStrokeColor(colors.HexColor("#b8ccd1"))
        c.line(44, 52, 551, 52)
        write(44, 35, "Procurement intake   /   Test record HLS-2601", 9, color=muted)
        write(502, 35, f"Page {number} of 2", 9, color=muted)

    def block(top, title, rows):
        c.setFillColor(colors.HexColor("#eef5f4"))
        c.roundRect(44, top - 30, 507, 28, 6, fill=1, stroke=0)
        write(54, top - 20, title, 11, True, accent)
        y = top - 55
        for label, value, stacked in rows:
            if stacked:
                write(54, y, label + ":", 10, True, muted)
                write(76, y - 17, value)
                y -= 44
            else:
                write(54, y, label + ": " + value)
                y -= 26
        return y

    header("Company identity and registrations", 1)
    y = block(704, "01  Company profile", [
        ("Supplier trading name", "Harborline Supplies", False),
        ("Registered entity name", "Harborline Lab Supplies Pvt Ltd", True),
        ("Tax residence country", "India", False),
        ("Registered office", "21 Example Wharf, Mumbai, Maharashtra 400999", False),
        ("Business activity", "Laboratory consumables and maintenance supplies", False),
        ("Supplier type", "Private limited company", False),
    ])
    y = block(y - 14, "02  Registrations and contacts", [
        ("GST identification number", "27ABCDE1234F1Z7", False),
        ("PAN", "ABCDE1234F", False),
        ("Accounts payable email", "accounts@harborline.example", False),
        ("Contact role", "Vendor finance desk", False),
        ("MSME registration", "Not applicable", False),
    ])
    write(54, y - 14, "Supporting documents are submitted separately for review.", 9, color=muted)
    c.showPage()

    header("Payment instructions and declaration", 2)
    y = block(704, "03  Bank and remittance details", [
        ("Bank name", "Fictional Demonstration Bank", False),
        ("Beneficiary name", "Harborline Lab Supplies Pvt Ltd", False),
        ("Remittance account number", "007451236809", False),
        ("Branch code (IFSC)", "DEMO0004321", False),
        ("Account type", "Current account", False),
        ("Bank branch", "Sample Branch, Mumbai, Maharashtra", False),
        ("Payment currency", "INR", False),
        ("Payment terms", "30 days from approved invoice", False),
    ])
    y = block(y - 18, "04  Supplier declaration", [
        ("Representative", "Sample Finance Representative", False),
        ("Role", "Finance operations", False),
        ("Date", "23 November 2026", False),
    ])
    write(54, y - 11, "The supplier declares the details above for onboarding review.", 10, color=muted)
    write(54, y - 34, "Signature: Sample Vendor Representative", 10, True)
    c.save()


def scanned_copy(source, path):
    """Rasterize each page to create a fixture without a text layer."""
    import pypdfium2

    document = pypdfium2.PdfDocument(source)
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle("Supplier Registration Form - Synthetic Scan")
    c.setAuthor("Vendor Onboarding Demo")
    try:
        for page in document:
            bitmap = page.render(scale=2.2)
            try:
                image = io.BytesIO()
                bitmap.to_pil().convert("RGB").save(image, format="JPEG", quality=87)
                image.seek(0)
                c.drawImage(ImageReader(image), 0, 0, width=A4[0], height=A4[1])
                c.showPage()
            finally:
                bitmap.close()
                page.close()
    finally:
        document.close()
    c.save()


def conflicting_bank_packet(path):
    """Create a vendor attachment containing both current and archived confirmations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle("Juniper Bank Confirmation Packet - Synthetic Sample")
    c.setAuthor("Vendor Onboarding Demo")
    ink = colors.HexColor("#152d49")
    muted = colors.HexColor("#45576d")
    edge = colors.HexColor("#cbd8e3")
    rows = (
        ("Current confirmation", "23 November 2026", "FDB-2026-1123", "001234567890"),
        ("Archived confirmation", "18 May 2026", "FDB-2026-0518", "001234569999"),
    )

    def write(x, y, value, size=11, bold=False, color=ink):
        c.setFillColor(color)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x, y, value)

    for page, (caption, date, reference, account) in enumerate(rows, 1):
        c.setFillColor(ink)
        c.rect(0, 831, A4[0], 11, fill=1, stroke=0)
        write(48, 776, "Fictional Demonstration Bank", 22, True)
        write(48, 754, "Sample Branch  |  Bengaluru, Karnataka", 10, color=muted)
        write(48, 731, "SYNTHETIC SAMPLE  /  VENDOR ONBOARDING TEST", 9, True, muted)
        write(48, 681, "Bank Account Confirmation", 19, True)
        write(48, 657, caption, 11, True)
        write(48, 631, f"Issued: {date}     Reference: {reference}", 10, color=muted)
        write(48, 588, "To the Vendor Onboarding Team,")
        write(48, 566, "We confirm the account details below as recorded on the issue date.")
        c.setFillColor(colors.HexColor("#eaf1f7"))
        c.roundRect(48, 509, 499, 32, 5, fill=1, stroke=0)
        write(60, 520, "Account details", 11, True)
        for y, (label, value) in zip(range(479, 326, -29), (
            ("Account holder:", "Juniper Office Systems Pvt Ltd"),
            ("Account number:", account),
            ("Account type:", "Current account"),
            ("IFSC:", "DEMO0001234"),
            ("Branch:", "Sample Branch, Bengaluru, Karnataka"),
            ("Currency:", "INR"),
        )):
            write(60, y, label, 10, True)
            write(196, y, value)
            c.setStrokeColor(edge)
            c.line(48, y - 10, 547, y - 10)
        write(48, 287, "This confirmation was prepared for a procurement records request.", 10, color=muted)
        write(48, 242, "Sample Branch Operations", 11, True)
        write(48, 224, "Fictional Demonstration Bank", 10, color=muted)
        c.setStrokeColor(edge)
        c.line(48, 62, 547, 62)
        write(48, 44, "Fictional test data. No real bank account or payment instructions.", 9, color=muted)
        write(502, 44, f"{page} / 2", 9, color=muted)
        c.showPage()
    c.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", action="store_true", help="also rasterize the supplier registration fixture")
    args = parser.parse_args()
    two_page_match(OUT / "pdf_two_page_match.pdf")
    account_mismatch(OUT / "pdf_account_mismatch.pdf")
    image_only(OUT / "pdf_image_only.pdf")
    invalid(OUT / "invalid.pdf")
    onboarding_form(OUT / "vendor_onboarding_sample.pdf")
    onboarding_form(OUT / "vendor_onboarding_needs_review.pdf", conflicting_account=True)
    supplier_registration_form(OUT / "supplier_registration_v2.pdf")
    if args.scan:
        scanned_copy(OUT / "supplier_registration_v2.pdf", OUT / "supplier_registration_scan.pdf")
    conflicting_bank_packet(DEMO_PDFS / "juniper_conflicting_bank_confirmations.pdf")
    for p in sorted(OUT.iterdir()):
        print(f"{p.name:28s} {p.stat().st_size} bytes")


if __name__ == "__main__":
    main()
