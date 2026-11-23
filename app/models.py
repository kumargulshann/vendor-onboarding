"""
Vendor onboarding domain model + input schema.

A "vendor submission" is the real input the process accepts. In a demo you can
submit it through the website or the JSON API.
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
    bank_routing: Optional[str] = None     # IFSC for Indian accounts
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
    "bank account holder name", "bank account holder", "accountholder",
    "name on account", "beneficiary", "titled to",
]
_ACCOUNT_LABELS = [
    "account number", "account no", "account no.", "account #",
    "bank account number", "bank account no.", "bank account no", "acc no",
]
_IFSC_LABELS = ["ifsc code of branch", "branch ifsc", "ifsc code", "ifsc"]
_EMPTY_FIELD_VALUES = {"", "n/a", "na", "not applicable", "none"}
_VENDOR_FORM_TITLE = re.compile(
    r"(?im)^[ \t]*(?:vendor|supplier)[ \t]+"
    r"(?:onboarding(?:[ \t]+details)?|registration)[ \t]+forms?[ \t:]*$"
)


def is_vendor_declaration(text: str) -> bool:
    """Recognize the onboarding declaration without matching a letter's addressee."""
    return bool(_VENDOR_FORM_TITLE.search(text))


def _label_values(text: str, labels: list[str]) -> list[str]:
    alternatives = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    pattern = (
        rf"(?im)^[^\S\r\n]*(?:{alternatives})"
        r"(?:[^\S\r\n]*[:#–—-][^\S\r\n]*|[^\S\r\n]+)([^\r\n;]+)"
    )
    return [match.strip() for match in re.findall(pattern, text)]


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
    """Return a single unambiguous holder from the letter."""
    holders = _label_values(text or "", _HOLDER_LABELS)
    if not holders or any(
        len(name) > 120 or not any(ch.isalpha() for ch in name)
        or re.search(r"\d(?:[ .-]*\d){3,}", name)
        for name in holders
    ):
        return None
    return holders[0] if len({normalize_name(name) for name in holders}) == 1 else None


def normalize_account(value: str | None) -> str:
    """
    Normalise an account number to a single comparable value.

    Keeps digits and letters (preserving leading zeros and alphanumeric
    accounts), strips ordinary formatting separators (spaces, hyphens, dots,
    etc.), and returns "" for empty, whitespace-only, or punctuation-only input
    (e.g. an em dash) so it is treated as missing/unreadable.
    """
    if not value or not re.fullmatch(r"[A-Za-z0-9 \t\u00a0.–—-]+", value.strip()):
        return ""
    normalized = re.sub(r"[^0-9A-Za-z]", "", value).upper()
    if re.fullmatch(r"X{3,}\d{1,4}", normalized):
        return ""
    return normalized if any(ch.isdigit() for ch in normalized) else ""


def _bank_account_details(text: str) -> tuple[str | None, bool]:
    """Return the single usable account and whether readable accounts conflict."""
    accounts = {normalize_account(value) for value in _label_values(text or "", _ACCOUNT_LABELS)}
    readable = accounts - {""}
    account = next(iter(readable)) if len(readable) == len(accounts) == 1 else None
    return account, len(readable) > 1


def extract_bank_account(text: str) -> Optional[str]:
    """Return the normalized account number found in a bank-proof letter, or None."""
    account, _ = _bank_account_details(text)
    return account


def normalize_ifsc(value: str | None) -> str | None:
    normalized = re.sub(r"[ \t\u00a0]", "", value or "").upper()
    return normalized if re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", normalized) else None


def parse_bank_letter(text: str) -> dict:
    """Build the bank evidence shared by the rules, result, and saved record."""
    if is_vendor_declaration(text):
        return {"kind": "vendor_declaration", "holder": None, "account": None,
                "account_conflict": False, "ifsc": None, "ifsc_listed": False}
    account, account_conflict = _bank_account_details(text)
    ifsc_values = [
        value for value in _label_values(text, _IFSC_LABELS)
        if value.strip(" :-–—").casefold() not in _EMPTY_FIELD_VALUES
    ]
    ifsc_candidates = {normalize_ifsc(value) for value in ifsc_values}
    ifsc = next(iter(ifsc_candidates)) if len(ifsc_candidates) == 1 else None
    return {
        "kind": "bank_proof" if text.strip() else "none",
        "holder": extract_bank_holder(text),
        "account": account,
        "account_conflict": account_conflict,
        "ifsc": ifsc,
        "ifsc_listed": bool(ifsc_values),
    }


def mask_ending(value: str | None, keep: int = 4) -> str:
    """Mask an account number, showing only the last `keep` characters."""
    if not value:
        return ""
    s = str(value)
    if len(s) <= keep:
        return "*" * len(s)
    return "*" * (len(s) - keep) + s[-keep:]
