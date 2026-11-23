"""Suggest declared values from searchable or image-only onboarding forms."""
from __future__ import annotations

import re

from .models import TAX_ID_FORMATS, is_vendor_declaration, normalize_account, normalize_ifsc
from .pdf_evidence import PdfEvidenceError, extract_pdf_pages, read_scanned_form


FIELD_LABELS = {
    "vendor_name": ("Full Name of Vendor/ Supplier", "Full Name of Vendor/Supplier",
                    "Vendor/Supplier Name", "Vendor Name", "Supplier Name",
                    "Supplier Trading Name", "Trading Name"),
    "legal_name": ("Registered Legal Name", "Registered Company Name", "Legal Name",
                   "Registered Entity Name", "Legal Business Name"),
    "country": ("Country of Registration", "Country", "Tax Residence Country"),
    "tax_id": ("GST Registration Number", "GST Registration No.", "GSTIN",
               "GST Identification Number", "Tax Registration Number"),
    "bank_holder_name": ("Bank Account Holder Name", "Bank Account Holder",
                         "Account Holder Name", "Account Holder", "Name on Account", "Beneficiary Name"),
    "bank_account_number": ("Bank Acocunt No", "Bank Account No.", "Bank Account No",
                            "Bank Account Number", "Account Number", "Remittance Account Number",
                            "Beneficiary Account Number"),
    "bank_routing": ("IFSC Code of branch", "Branch IFSC", "IFSC Code", "IFSC", "Branch Code (IFSC)"),
    "contact_email": ("Contact Email", "E-mail ID", "Email ID", "E-mail", "Email", "Accounts Payable Email"),
}
COUNTRIES = {
    "india": "IN", "united states": "US", "united states of america": "US",
    "usa": "US", "united kingdom": "GB", "uk": "GB", "germany": "DE",
}
EMPTY_VALUES = {"", "-", "—", "n/a", "na", "not applicable", "none"}
LABEL_PATTERNS = {
    field: re.compile(
        r"^[ \t]*(?:" + "|".join(
            r"[ \t]+".join(map(re.escape, label.split()))
            for label in sorted(labels, key=len, reverse=True)
        ) + r")(?:(?:[ \t]*[:–—][ \t]*|[ \t]{2,})(?P<value>.*?))?[ \t]*$",
        re.IGNORECASE,
    )
    for field, labels in FIELD_LABELS.items()
}


def _field_value(field: str, raw: str, country: str | None) -> str | None:
    if len(raw) > 500:
        return None
    if field in ("vendor_name", "legal_name", "bank_holder_name"):
        return raw if any(character.isalpha() for character in raw) else None
    if field == "country":
        code = COUNTRIES.get(raw.casefold(), raw.upper())
        return code if code in TAX_ID_FORMATS else None
    if field == "contact_email":
        return raw if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw) else None
    if field == "bank_account_number":
        value = normalize_account(raw)
        return value if len(value) >= 5 else None
    if field == "bank_routing":
        return normalize_ifsc(raw)
    # GST registrations in the supported template are Indian GSTINs.
    value = raw.upper()
    return value if country in ("IN", None) and re.fullmatch(
        TAX_ID_FORMATS["IN"]["pattern"], value,
    ) else None


def import_onboarding_form(content: bytes) -> dict:
    """Return editable candidates and page references, without storing the PDF."""
    pages = extract_pdf_pages(content, layout=True, document="vendor onboarding form")
    method = "text"
    if not any(page.strip() for page in pages):
        pages = read_scanned_form(content, len(pages))
        method = "ocr"

    candidates = {field: [] for field in FIELD_LABELS}
    for page_number, page in enumerate(pages, 1):
        lines = page.splitlines()
        for index, line in enumerate(lines):
            for field, pattern in LABEL_PATTERNS.items():
                match = pattern.fullmatch(line)
                if not match:
                    continue
                value = (match["value"] or "").strip()
                if not value and index + 1 < len(lines):
                    next_line = lines[index + 1]
                    indent = len(line) - len(line.lstrip(" \t"))
                    next_indent = len(next_line) - len(next_line.lstrip(" \t"))
                    # A stacked value is indented and cannot be a separate row.
                    if (next_indent >= indent + 4 and ":" not in next_line
                            and not any(p.fullmatch(next_line) for p in LABEL_PATTERNS.values())):
                        value = next_line.strip()
                candidates[field].append((value, page_number))
                break

    title_found = any(is_vendor_declaration(page) for page in pages)
    if not title_found and not ((candidates["vendor_name"] or candidates["legal_name"]) and (
        candidates["tax_id"] or candidates["bank_routing"] or candidates["country"]
    )):
        raise PdfEvidenceError("This PDF does not look like a supported onboarding form. Enter the details manually.")

    fields = {}
    # Read country first because it provides context for the GST field.
    for field in ("country", *(field for field in FIELD_LABELS if field != "country")):
        supplied = [(raw, page) for raw, page in candidates[field] if raw.casefold() not in EMPTY_VALUES]
        country = fields.get("country", {}).get("value")
        values = [_field_value(field, raw, country) for raw, _ in supplied]
        distinct = {value.casefold() for value in values if value is not None}
        result = {"status": "missing", "value": None, "pages": sorted({p for _, p in candidates[field]})}
        if len(distinct) > 1:
            result["status"] = "ambiguous"
        elif supplied and (not distinct or None in values):
            result["status"] = "needs_review"
        elif distinct:
            result.update(status="extracted", value=values[0])
        fields[field] = result

    return {"page_count": len(pages), "method": method, "fields": fields}
