"""
Deterministic validation + consistency rule engine.

This is the heart of the process and the part we can explain most clearly to a
buyer: each check has a reason, and the output ties every decision back to the
rule that produced it. Deterministic logic is the backbone so the live demo
never depends on an external service being available.

The bank letter is parsed ONCE by the runner and passed in as `evidence` so the
rules, the displayed result and the saved record all use the same extraction.

Decision model:
  Each check is either a pass, or a failure with a `block` level:
    - "reject": a hard, unverifiable signal (e.g. malformed tax ID, unsupported
      country). The submission cannot be trusted -> rejected.
    - "pending": something is missing or needs vendor action -> pending.
  No blocking failures -> approved. Warnings never block.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from .models import (VendorSubmission, TAX_ID_FORMATS, REQUIRED_DOCUMENTS,
                     normalize_name, normalize_account, mask_ending)
from .ai_layer import parse_bank_letter


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    severity: str = "error"   # "error" blocks, "warning" does not
    block: str = "pending"    # "reject" or "pending" (only meaningful if failed)


@dataclass
class RuleResult:
    checks: list[Check] = field(default_factory=list)

    @property
    def errors(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity == "error"]

    @property
    def reject_errors(self) -> list[Check]:
        return [c for c in self.errors if c.block == "reject"]

    @property
    def pending_errors(self) -> list[Check]:
        return [c for c in self.errors if c.block == "pending"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors


def _name_matches(bank_holder: str, legal_name: str | None,
                  trading_name: str | None) -> tuple[bool, str, str]:
    """
    Compare the bank account holder to the registered legal name when supplied.
    When the legal name is missing we fall back to the trading name and flag the
    gap, so the behaviour is defined instead of silently assumed.
    Returns (matches, comparison_basis, normalized_target).
    """
    if legal_name and legal_name.strip():
        basis = "legal name"
        target = legal_name
    else:
        basis = "trading name"
        target = trading_name or ""
    return normalize_name(bank_holder) == normalize_name(target), basis, normalize_name(target)


def validate(sub: VendorSubmission, evidence: dict | None = None) -> RuleResult:
    """
    Run all checks. `evidence` is the single-pass bank-letter parse
    ({"holder", "account"}); if omitted it is parsed here.
    """
    res = RuleResult()

    # single-pass evidence (shared with the runner and the saved record)
    if evidence is None:
        evidence = parse_bank_letter(sub.bank_proof_text or "")
    letter_holder = evidence.get("holder")
    letter_account = evidence.get("account")

    # 1. Required identity fields
    res.checks.append(Check(
        "vendor_name_present",
        bool(sub.vendor_name and sub.vendor_name.strip()),
        "Vendor name is present" if sub.vendor_name else "Vendor name is missing"))

    # 2. Country present + supported
    country_ok = sub.country in TAX_ID_FORMATS
    res.checks.append(Check(
        "country_supported",
        country_ok,
        f"Country '{sub.country}' is supported" if country_ok
        else f"Country '{sub.country}' has no tax-ID rule (unsupported)",
        block="reject"))

    # 3. Tax-ID: distinguish MISSING (pending, ask for it) from MALFORMED
    #    (reject, cannot verify). This is a deliberate, explainable distinction.
    if not sub.tax_id:
        res.checks.append(Check(
            "tax_id_format", False,
            "Tax ID is missing — please provide your registered tax ID",
            block="pending"))
    elif sub.country in TAX_ID_FORMATS:
        spec = TAX_ID_FORMATS[sub.country]
        ok = bool(re.compile(spec["pattern"]).match(sub.tax_id))
        res.checks.append(Check(
            "tax_id_format", ok,
            f"Tax ID matches {spec['name']} format" if ok
            else f"Tax ID does not match the {spec['name']} format for country "
                 f"'{sub.country}' ({spec['desc']}) — cannot be verified",
            block="reject"))
    else:
        res.checks.append(Check(
            "tax_id_format", False,
            "Tax ID present but country not recognised",
            block="reject"))

    # 4. Banking: account holder REQUIRED before approval
    res.checks.append(Check(
        "bank_holder_present",
        bool(sub.bank_holder_name and sub.bank_holder_name.strip()),
        "Bank account holder name is present" if sub.bank_holder_name
        else "Bank account holder name is missing",
        block="pending"))

    # 5. Banking: account number present AND readable (normalized). Empty,
    #    whitespace-only or punctuation-only (e.g. an em dash) counts as
    #    missing/unreadable so it cannot be silently approved.
    form_acc_norm = normalize_account(sub.bank_account_number)
    if not sub.bank_account_number or not sub.bank_account_number.strip():
        acct_present_detail = "Bank account number is missing — please provide a readable account number"
    elif not form_acc_norm:
        acct_present_detail = "Bank account number is unreadable — please provide a readable account number"
    else:
        acct_present_detail = "Bank account number present and readable"
    res.checks.append(Check(
        "bank_account_present", bool(form_acc_norm), acct_present_detail,
        block="pending"))

    # 6. Cross-field: bank holder vs registered legal name (fallback trading
    #    name). The classic fraud signal — subtle but high-impact.
    if sub.bank_holder_name and (sub.legal_name or sub.vendor_name):
        matches, basis, target_norm = _name_matches(
            sub.bank_holder_name, sub.legal_name, sub.vendor_name)
        if not target_norm:
            res.checks.append(Check(
                "bank_holder_match", False,
                "Cannot compare names — the registered name is empty/unreadable",
                block="pending"))
        else:
            res.checks.append(Check(
                "bank_holder_match", matches,
                f"Bank account holder matches the {basis}" if matches else
                f"Bank account holder does NOT match the {basis} "
                f"(possible mismatch)",
                block="pending"))
    else:
        res.checks.append(Check(
            "bank_holder_match", False,
            "Cannot compare names — bank holder or identifying name not provided",
            block="pending"))

    # 7. Bank-proof document: use the shared evidence for name + account.
    source = (sub.bank_evidence_source or "text").lower()
    if not sub.bank_proof_text:
        if source == "pdf":
            detail = ("The uploaded PDF contains no readable text (image-only) - "
                      "please provide a readable bank letter")
        else:
            detail = ("No bank-proof document provided - please attach a bank "
                      "letter/statement")
        res.checks.append(Check("bank_proof_document", False, detail,
                                block="pending"))
    else:
        if not letter_holder:
            res.checks.append(Check(
                "bank_proof_document", False,
                "Could not read an account-holder name from the bank-proof "
                "document — please provide a clear bank letter",
                block="pending"))
        else:
            matches, basis, target_norm = _name_matches(
                letter_holder, sub.legal_name, sub.vendor_name)
            if not target_norm:
                res.checks.append(Check(
                    "bank_proof_document", False,
                    f"Bank-proof document account holder '{letter_holder}' "
                    f"could not be compared — the {basis} is empty/unreadable",
                    block="pending"))
            else:
                res.checks.append(Check(
                    "bank_proof_document", matches,
                    f"Bank-proof document account holder '{letter_holder}' "
                    f"matches the {basis}" if matches else
                    f"Bank-proof document account holder '{letter_holder}' "
                    f"does NOT match the {basis} — evidence conflicts with the "
                    f"submission",
                    block="pending"))

        # 7b. Account number cross-check (only when both sources are readable)
        submitted_acc = normalize_account(sub.bank_account_number)
        if submitted_acc and letter_account:
            acc_ok = submitted_acc == letter_account
            res.checks.append(Check(
                "bank_account_match", acc_ok,
                f"Account number in submission ({mask_ending(sub.bank_account_number)}) "
                f"matches the account in the bank letter ({mask_ending(letter_account)})"
                if acc_ok else
                f"Account number in submission "
                f"({mask_ending(sub.bank_account_number)}) does NOT match the "
                f"account in the bank letter ({mask_ending(letter_account)}) — "
                f"payment account needs review",
                block="pending"))
        elif submitted_acc and not letter_account:
            res.checks.append(Check(
                "bank_account_match", False,
                "No account number found in the bank letter to cross-check against",
                block="pending"))

    # 8. Required compliance documents (label-based completeness)
    missing = REQUIRED_DOCUMENTS - set(sub.documents or [])
    res.checks.append(Check(
        "documents_complete", not missing,
        "All required documents provided" if not missing
        else f"Missing required documents: {', '.join(sorted(missing))}",
        block="pending"))

    # 9. Contact email sanity (warning only — not blocking)
    email_ok = bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", sub.contact_email or ""))
    res.checks.append(Check(
        "contact_email_valid", email_ok,
        "Contact email is valid" if email_ok else "Contact email is missing/invalid",
        severity="warning"))

    return res
