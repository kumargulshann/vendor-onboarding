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
from .models import (VendorSubmission, TAX_ID_FORMATS, REQUIRED_DOCUMENTS, DOCUMENT_LABELS,
                     normalize_name, normalize_account, normalize_ifsc,
                     mask_ending, parse_bank_letter)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    severity: str = "error"   # "error" blocks, "warning" does not
    block: str = "pending"    # "reject" or "pending" (only meaningful if failed)
    # Allowlisted finding for explanations. Never copy submitted values or detail here.
    explanation: str = ""


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
    Use the trading name when the registered name is missing.
    Returns (matches, comparison_basis, normalized_target).
    """
    if legal_name and legal_name.strip():
        basis = "legal name"
        target = legal_name
    else:
        basis = "trading name"
        target = trading_name or ""
    return normalize_name(bank_holder) == normalize_name(target), basis, normalize_name(target)


def validate(sub: VendorSubmission, evidence: dict | None = None, *, require_ifsc: bool = False) -> RuleResult:
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
    vendor_name_present = bool(normalize_name(sub.vendor_name))
    res.checks.append(Check(
        "vendor_name_present",
        vendor_name_present,
        "Vendor name is present" if vendor_name_present
        else "Please provide the vendor's trading name",
        explanation="" if vendor_name_present else "The vendor trading name is missing."))

    # 2. Country present + supported
    country_ok = sub.country in TAX_ID_FORMATS
    res.checks.append(Check(
        "country_supported",
        country_ok,
        f"Country '{sub.country}' is supported" if country_ok
        else f"Country '{sub.country}' has no tax-ID rule (unsupported)",
        block="reject",
        explanation="" if country_ok else "The submitted country has no supported tax ID check."))

    # 3. Tax-ID: distinguish MISSING (pending, ask for it) from MALFORMED
    #    (reject, cannot verify). This is a deliberate, explainable distinction.
    if not sub.tax_id:
        res.checks.append(Check(
            "tax_id_format", False,
            "Tax ID is missing — please provide your registered tax ID",
            block="pending", explanation="The registered tax ID is missing."))
    elif sub.country in TAX_ID_FORMATS:
        spec = TAX_ID_FORMATS[sub.country]
        ok = bool(re.fullmatch(spec["pattern"], sub.tax_id))
        res.checks.append(Check(
            "tax_id_format", ok,
            f"Tax ID matches {spec['name']} format" if ok
            else f"Tax ID does not match the {spec['name']} format for country "
                 f"'{sub.country}' ({spec['desc']}) — cannot be verified",
            block="reject",
            explanation=(
                f"The tax ID matches the {spec['name']} format." if ok
                else f"The tax ID does not match the required {spec['name']} format."
            )))
    else:
        res.checks.append(Check(
            "tax_id_format", False,
            "Tax ID present but country not recognised",
            block="reject", explanation="The tax ID format could not be checked for the submitted country."))

    # 4. Banking: account holder REQUIRED before approval
    bank_holder_present = bool(normalize_name(sub.bank_holder_name))
    res.checks.append(Check(
        "bank_holder_present",
        bank_holder_present,
        "Bank account holder name is present" if bank_holder_present
        else "Please provide the bank account holder's name",
        explanation="" if bank_holder_present else "The submission is missing the bank account holder name."))

    # 5. Banking: account number present AND readable (normalized). Empty,
    #    whitespace-only or punctuation-only (e.g. an em dash) counts as
    #    missing/unreadable so it cannot be silently approved.
    form_acc_norm = normalize_account(sub.bank_account_number)
    if not sub.bank_account_number or not sub.bank_account_number.strip():
        acct_present_detail = "Bank account number is missing — please provide a readable account number"
        acct_explanation = "The submission is missing the bank account number."
    elif not form_acc_norm:
        acct_present_detail = "Bank account number is unreadable — please provide a readable account number"
        acct_explanation = "The submitted bank account number is unreadable."
    else:
        acct_present_detail = "Bank account number present and readable"
        acct_explanation = ""
    res.checks.append(Check(
        "bank_account_present", bool(form_acc_norm), acct_present_detail,
        explanation=acct_explanation))

    # 6. Cross-field: bank holder vs registered legal name (fallback trading
    #    name). The classic fraud signal — subtle but high-impact.
    if sub.bank_holder_name and (sub.legal_name or sub.vendor_name):
        matches, basis, target_norm = _name_matches(
            sub.bank_holder_name, sub.legal_name, sub.vendor_name)
        if not target_norm:
            res.checks.append(Check(
                "bank_holder_match", False,
                "Cannot compare names — the registered name is empty/unreadable",
                explanation="The account holder could not be compared because the vendor name is unreadable."))
        else:
            res.checks.append(Check(
                "bank_holder_match", matches,
                f"Bank account holder matches the {basis}" if matches else
                f"Bank account holder does NOT match the {basis} "
                f"(possible mismatch)",
                explanation="" if matches else f"The submitted account holder differs from the vendor's {basis}."))
    else:
        res.checks.append(Check(
            "bank_holder_match", False,
            "Cannot compare names — bank holder or identifying name not provided",
            explanation="The account holder could not be compared because the identifying name is missing."))

    # 7. Bank-proof document: use the shared evidence for name + account.
    source = (sub.bank_evidence_source or "text").lower()
    if evidence.get("kind") == "vendor_declaration":
        res.checks.append(Check(
            "bank_proof_document", False,
            "The uploaded document is a vendor onboarding form. Provide a separate bank letter or statement.",
            explanation="The bank-proof upload is the vendor onboarding form.",
        ))
    elif not sub.bank_proof_text:
        if source == "pdf":
            detail = ("The uploaded PDF contains no readable text (image-only) - "
                      "please provide a readable bank letter")
        else:
            detail = ("No bank-proof document provided - please attach a bank "
                      "letter/statement")
        res.checks.append(Check(
            "bank_proof_document", False, detail,
            explanation=(
                "The bank-proof PDF has no readable text." if source == "pdf"
                else "No bank letter or statement was supplied."
            )))
    else:
        if not letter_holder:
            res.checks.append(Check(
                "bank_proof_document", False,
                "Could not read an account-holder name from the bank-proof "
                "document — please provide a clear bank letter",
                explanation="An account holder name could not be read from the bank proof."))
        else:
            matches, basis, target_norm = _name_matches(
                letter_holder, sub.legal_name, sub.vendor_name)
            if not target_norm:
                res.checks.append(Check(
                    "bank_proof_document", False,
                    f"Bank-proof document account holder '{letter_holder}' "
                    f"could not be compared — the {basis} is empty/unreadable",
                    explanation=f"The name from the bank proof could not be compared with the vendor's {basis}."))
            else:
                res.checks.append(Check(
                    "bank_proof_document", matches,
                    f"Bank-proof document account holder '{letter_holder}' "
                    f"matches the {basis}" if matches else
                    f"Bank-proof document account holder '{letter_holder}' "
                    f"does NOT match the {basis} — evidence conflicts with the "
                    f"submission",
                    explanation=(
                        f"The account holder in the bank proof matches the vendor's {basis}." if matches
                        else f"The account holder in the bank proof differs from the vendor's {basis}."
                    )))

        # 7b. Account number cross-check (only when both sources are readable)
        if form_acc_norm and letter_account:
            acc_ok = form_acc_norm == letter_account
            res.checks.append(Check(
                "bank_account_match", acc_ok,
                f"Account number in submission ({mask_ending(form_acc_norm)}) "
                f"matches the account in the bank letter ({mask_ending(letter_account)})"
                if acc_ok else
                f"Account number in submission "
                f"({mask_ending(form_acc_norm)}) does NOT match the "
                f"account in the bank letter ({mask_ending(letter_account)}) — "
                f"payment account needs review",
                explanation=(
                    "The submitted account number matches the bank letter." if acc_ok
                    else "The submitted account number differs from the bank letter."
                )))
        elif form_acc_norm and not letter_account:
            detail = "Please provide a bank letter with one clear, complete account number"
            if evidence.get("account_conflict"):
                detail = (
                    "The bank proof contains conflicting account numbers. "
                    "Upload a current bank letter showing only the intended payment account."
                )
            res.checks.append(Check(
                "bank_account_match", False, detail,
                explanation=(
                    "The bank proof contains conflicting account numbers."
                    if evidence.get("account_conflict")
                    else "The bank proof has no single readable account number."
                )))

    # A correction preserves any IFSC check the case already required.
    declared_ifsc = normalize_ifsc(sub.bank_routing)
    proof_ifsc = evidence.get("ifsc")
    if require_ifsc or (sub.country == "IN" and (sub.bank_routing or "").strip()
                        and evidence.get("ifsc_listed")):
        if not declared_ifsc or not proof_ifsc:
            res.checks.append(Check(
                "bank_ifsc_match", False,
                "Provide a single readable IFSC in both the submission and the bank letter.",
                explanation=(
                    "A readable IFSC is missing from both the submission and bank proof."
                    if not declared_ifsc and not proof_ifsc
                    else "The submitted IFSC is missing or unreadable." if not declared_ifsc
                    else "The bank proof has no single readable IFSC."
                ),
            ))
        else:
            matches = declared_ifsc == proof_ifsc
            res.checks.append(Check(
                "bank_ifsc_match", matches,
                "The submitted IFSC matches the bank letter." if matches
                else "The submitted IFSC differs from the bank letter. Confirm the branch code and provide matching bank proof.",
                explanation=(
                    "The submitted IFSC matches the bank letter." if matches
                    else "The submitted IFSC differs from the bank letter."
                ),
            ))

    # 8. Required compliance documents (label-based completeness)
    missing = REQUIRED_DOCUMENTS - set(sub.documents or [])
    res.checks.append(Check(
        "documents_complete", not missing,
        "All required documents provided" if not missing
        else f"Missing required documents: {', '.join(sorted(missing))}",
        explanation=(
            "The required document checklist is complete." if not missing
            else "The checklist is missing " + " and ".join(DOCUMENT_LABELS[d] for d in sorted(missing)) + "."
        )))

    # 9. Contact email sanity (warning only — not blocking)
    email_ok = bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", sub.contact_email or ""))
    res.checks.append(Check(
        "contact_email_valid", email_ok,
        "Contact email is valid" if email_ok else "Contact email is missing/invalid",
        severity="warning",
        explanation="" if email_ok else "The contact email is missing or invalid."))

    return res
