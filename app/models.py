"""
Vendor onboarding domain model + input schema.

A "vendor submission" is the real input the process accepts. In a demo you can
submit this as JSON (a form) or drop a JSON file into submissions/.
"""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Optional


# Countries we support with their expected Tax-ID / VAT / GST format.
# Keeping this table is a deliberate design choice: format validation is
# jurisdiction-specific, and we can speak to WHY each rule exists.
TAX_ID_FORMATS = {
    "IN": {"name": "GSTIN", "pattern": r"^[0-9]{2}[A-Z0-9]{10}[A-Z0-9]{3}$",
           "desc": "15-char GSTIN (2-digit state code + 10-char PAN + 3-char entity/check)"},
    "US": {"name": "EIN", "pattern": r"^\d{2}-?\d{7}$",
           "desc": "9-digit EIN (XX-XXXXXXX)"},
    "GB": {"name": "VAT", "pattern": r"^GB\d{9}(?:\d{3})?$",
           "desc": "UK VAT (GB + 9 or 12 digits)"},
    "DE": {"name": "USt-IdNr", "pattern": r"^DE\d{9}$",
           "desc": "German VAT (DE + 9 digits)"},
}


@dataclass
class VendorSubmission:
    # --- identity ---
    vendor_name: str                       # trading name
    legal_name: Optional[str] = None       # registered legal name
    country: Optional[str] = None          # ISO-2, e.g. "IN", "US"
    tax_id: Optional[str] = None
    # --- banking (payment) ---
    bank_holder_name: Optional[str] = None # name on the bank account
    bank_account_number: Optional[str] = None
    bank_routing: Optional[str] = None     # IFSC/ABA/etc, loosely validated
    bank_proof_text: Optional[str] = None  # sample bank-proof letter (text)
    bank_evidence_source: str = "text"     # "text" (pasted) or "pdf" (uploaded)
    case_id: Optional[str] = None          # set to correct/append to an existing case
    expected_previous_run_id: Optional[str] = None  # the attempt the vendor thinks they're correcting
    # --- compliance documents ---
    documents: list[str] = field(default_factory=list)   # required doc kinds
    # --- contact / misc ---
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "VendorSubmission":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


REQUIRED_DOCUMENTS = {
    "incorporation_proof",   # certificate of incorporation / registration
    "bank_proof",            # bank statement / voided cheque
    "tax_registration",      # tax registration certificate
    "address_proof",         # utility bill / registered address
}

DOCUMENT_LABELS = {
    "incorporation_proof": "incorporation proof",
    "bank_proof": "bank proof",
    "tax_registration": "tax registration certificate",
    "address_proof": "address proof",
}


# --- bank-letter parsing -------------------------------------------------------
# Deterministic parsers for the sample bank letter. These are intentionally
# predictable so the approval rules stay reproducible; a model can be dropped in
# for messier, real-world letters without changing the decision logic.
_HOLDER_LABELS = [
    "account holder name", "name of account holder", "account holder",
    "accountholder", "name on account", "beneficiary", "titled to",
]
_ACCOUNT_LABELS = [
    "account number", "account no", "account no.", "account #",
    "acc no", "account number",
]


def _find_label_value(text: str, labels: list[str]) -> Optional[str]:
    """
    Find the first matching label and return the value that follows it.
    Labels are tried longest-first so 'account holder name:' matches the full
    label rather than stopping at 'account holder' and swallowing 'name:'.

    The value is captured up to a newline or semicolon, so punctuation that is
    part of a name (e.g. the comma in 'Harbor Tools, Inc.') is preserved.
    """
    if not text:
        return None
    for label in sorted(labels, key=len, reverse=True):
        m = re.search(re.escape(label) + r"\s*[:\-#]?\s*([^\n;]{1,120})",
                      text, re.IGNORECASE)
        if m:
            val = m.group(1).strip().strip(":").strip()
            if val:
                return val
    return None


def normalize_name(s: str) -> str:
    """
    Normalise a name for tolerant, case/spacing/punctuation-insensitive
    comparison while RETURNING meaningful Unicode marks (vowel marks,
    diacritics). NFKC collapses compatibility variants; we then keep letters,
    digits and combining marks so distinct names in any script do not collapse
    to the same value.
    """
    norm = unicodedata.normalize("NFKC", s or "").lower()
    return "".join(
        ch for ch in norm
        if ch.isalnum() or unicodedata.category(ch).startswith("M")
    )


def extract_bank_holder(text: str) -> Optional[str]:
    """Return the account-holder name found in a bank-proof letter, or None."""
    return _find_label_value(text or "", _HOLDER_LABELS)


def normalize_account(value: str | None) -> str:
    """
    Normalise an account number to a single comparable value.

    Keeps digits and letters (preserving leading zeros and alphanumeric
    accounts), strips ordinary formatting separators (spaces, hyphens, dots,
    etc.), and returns "" for empty, whitespace-only, or punctuation-only input
    (e.g. an em dash) so it is treated as missing/unreadable.
    """
    if value is None:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", value)


def extract_bank_account(text: str) -> Optional[str]:
    """Return the normalized account number found in a bank-proof letter, or None."""
    val = _find_label_value(text or "", _ACCOUNT_LABELS)
    return normalize_account(val) or None


def parse_bank_letter(text: str) -> dict:
    """Parse holder + account number from a bank letter in one pass."""
    return {
        "holder": extract_bank_holder(text),
        "account": extract_bank_account(text),
    }


def mask_ending(value: str | None, keep: int = 4) -> str:
    """Mask an account number, showing only the last `keep` characters."""
    if not value:
        return ""
    s = str(value)
    if len(s) <= keep:
        return "*" * len(s)
    return "*" * (len(s) - keep) + s[-keep:]
