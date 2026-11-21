"""
Day 5 evaluation fixtures: 16 synthetic vendor submissions, separate from the
five demo examples.

Run:
    python3 -m tests.make_eval_fixtures

Outputs:
    data/eval/eval_*.json          - one submission per case (form fields + pdf)
    data/eval/expected.json        - recorded expectations under the existing policy
    data/eval/pdf/eval_*.pdf       - PDFs for PDF-backed cases

Each case records: name, the submission data, the PDF filename (if any), and the
expected status + blocking reasons + vendor next actions, derived by hand from
the deterministic policy in app/rules.py.
"""
from __future__ import annotations
import json
import pathlib

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

BASE = pathlib.Path(__file__).resolve().parent.parent
EVAL = BASE / "data" / "eval"
PDF_DIR = EVAL / "pdf"
PDF_DIR.mkdir(parents=True, exist_ok=True)

LETTER_ACCOUNT = "1234567890"


def letter(holder, account=LETTER_ACCOUNT):
    return f"Account holder: {holder}\nAccount number: {account}"


def make_pdf(name, holder, account, pages=1):
    c = canvas.Canvas(str(PDF_DIR / name), pagesize=A4)
    c.drawString(72, 780, "BANK OF TEST")
    c.drawString(72, 760, "Statement of Account")
    if pages == 1:
        c.drawString(72, 740, "Account holder: " + holder)
        c.drawString(72, 720, "Account number: " + account)
        c.showPage()
    else:
        c.showPage()  # page 1 header only
        c.drawString(72, 780, "Account holder: " + holder)
        c.drawString(72, 760, "Account number: " + account)
        c.showPage()
    c.save()


def make_image_only(name):
    c = canvas.Canvas(str(PDF_DIR / name), pagesize=A4)
    c.setFillColorRGB(0.2, 0.4, 0.7)
    c.rect(120, 400, 300, 120, fill=1, stroke=0)
    c.showPage()
    c.save()


def base_data(**kw):
    d = {
        "vendor_name": "Acme Manufacturing Pvt Ltd",
        "legal_name": "Acme Manufacturing Pvt Ltd",
        "country": "IN",
        "tax_id": "29ABCDE1234F1Z5",
        "bank_holder_name": "Acme Manufacturing Pvt Ltd",
        "bank_account_number": LETTER_ACCOUNT,
        "documents": ["incorporation_proof", "bank_proof",
                      "tax_registration", "address_proof"],
    }
    d.update(kw)
    return d


CASES = []


def case(name, data, status, reasons, actions, pdf=None):
    CASES.append({
        "name": name, "pdf": pdf, "data": data,
        "expected": {"status": status, "blocking_reasons": reasons,
                     "vendor_actions": actions},
    })


# 1. Clean approval (trading == legal).
case("eval_01_clean_approved",
     base_data(bank_proof_text=letter("Acme Manufacturing Pvt Ltd")),
     status="approved", reasons=[], actions=[])

# 2. Clean approval where trading and legal names differ; holder == legal.
case("eval_02_trading_legal_differ",
     base_data(vendor_name="Sunrise Logistics",
               legal_name="Sunrise Logistics Ltd",
               bank_holder_name="Sunrise Logistics Ltd",
               bank_proof_text=letter("Sunrise Logistics Ltd")),
     status="approved", reasons=[], actions=[])

# 3. Account mismatch (form account differs from the letter).
case("eval_03_account_mismatch",
     base_data(bank_account_number="9999999999",
               bank_proof_text=letter("Acme Manufacturing Pvt Ltd")),
     status="pending",
     reasons=["bank_account_match"],
     actions=["Account number in submission (******9999) does NOT match the "
              "account in the bank letter (******7890) — payment account needs review"])

# 4. Missing documents (only 2 of 4).
case("eval_04_missing_docs",
     base_data(documents=["incorporation_proof", "bank_proof"],
               bank_proof_text=letter("Acme Manufacturing Pvt Ltd")),
     status="pending",
     reasons=["documents_complete"],
     actions=["Please provide your address proof and tax registration certificate."])

# 5. Conflicting holder name (holder != legal/trading). The bank-proof
#    document check ALSO compares the letter holder to the legal name, so it
#    fails too -- both are correct blocking reasons.
case("eval_05_holder_conflict",
     base_data(legal_name="Meridian Company Ltd",
               vendor_name="Meridian Company Ltd",
               bank_holder_name="Meridian Co",
               bank_proof_text=letter("Meridian Co")),
     status="pending",
     reasons=["bank_holder_match", "bank_proof_document"],
     actions=["Bank account holder does NOT match the legal name (possible mismatch)",
              "Bank-proof document account holder 'Meridian Co' does NOT match "
              "the legal name — evidence conflicts with the submission"])

# 6. Malformed tax ID -> rejected.
case("eval_06_malformed_taxid",
     base_data(tax_id="12345",
               bank_proof_text=letter("Acme Manufacturing Pvt Ltd")),
     status="rejected",
     reasons=["tax_id_format"],
     actions=["Tax ID does not match the GSTIN format for country 'IN' (15-char "
              "GSTIN (2-digit state code + 10-char PAN + 3-char entity/check)) — "
              "cannot be verified"])

# 7. Missing tax ID -> pending.
case("eval_07_missing_taxid",
     base_data(tax_id=None,
               bank_proof_text=letter("Acme Manufacturing Pvt Ltd")),
     status="pending",
     reasons=["tax_id_format"],
     actions=["Tax ID is missing — please provide your registered tax ID"])

# 8. Unsupported country -> rejected. The tax-ID check also fails because the
#    country is unrecognized (cannot validate the tax ID). Both are correct.
case("eval_08_unsupported_country",
     base_data(country="FR",
               bank_proof_text=letter("Acme Manufacturing Pvt Ltd")),
     status="rejected",
     reasons=["country_supported", "tax_id_format"],
     actions=["Country 'FR' has no tax-ID rule (unsupported)",
              "Tax ID present but country not recognised"])

# 9. Unreadable (image-only) PDF -> pending.
make_image_only("eval_pdf_unreadable.pdf")
case("eval_09_pdf_unreadable",
     base_data(bank_evidence_source="pdf", bank_proof_text=""),
     pdf="eval_pdf_unreadable.pdf",
     status="pending",
     reasons=["bank_proof_document"],
     actions=["The uploaded PDF contains no readable text (image-only) - please "
              "provide a readable bank letter"])

# 10. Multi-page PDF, match -> approved.
make_pdf("eval_pdf_multipage_match.pdf", "Acme Manufacturing Pvt Ltd",
         LETTER_ACCOUNT, pages=2)
case("eval_10_pdf_multipage_match",
     base_data(bank_evidence_source="pdf", bank_proof_text=""),
     pdf="eval_pdf_multipage_match.pdf",
     status="approved", reasons=[], actions=[])

# 11. Multiple simultaneous issues: missing docs + account mismatch.
case("eval_11_multi_issue_docs_account",
     base_data(documents=["incorporation_proof", "bank_proof"],
               bank_account_number="9999999999",
               bank_proof_text=letter("Acme Manufacturing Pvt Ltd")),
     status="pending",
     reasons=["documents_complete", "bank_account_match"],
     actions=["Please provide your address proof and tax registration certificate.",
              "Account number in submission (******9999) does NOT match the "
              "account in the bank letter (******7890) — payment account needs review"])

# 12. Holder conflict + missing docs. The bank-proof document check also fails
#     (letter holder != legal name). All three are correct blocking reasons.
case("eval_12_holder_and_docs",
     base_data(legal_name="Meridian Company Ltd",
               vendor_name="Meridian Company Ltd",
               bank_holder_name="Meridian Co",
               documents=["incorporation_proof", "bank_proof"],
               bank_proof_text=letter("Meridian Co")),
     status="pending",
     reasons=["documents_complete", "bank_holder_match", "bank_proof_document"],
     actions=["Please provide your address proof and tax registration certificate.",
              "Bank account holder does NOT match the legal name (possible mismatch)",
              "Bank-proof document account holder 'Meridian Co' does NOT match "
              "the legal name — evidence conflicts with the submission"])

# 13. Formatting differences in the account (dashes/spaces) that still match.
case("eval_13_formatted_account_match",
     base_data(bank_account_number="1234-5678 90",
               bank_proof_text=letter("Acme Manufacturing Pvt Ltd")),
     status="approved", reasons=[], actions=[])

# 14. Unicode holder name (vowel marks) that matches the legal name.
case("eval_14_unicode_name_match",
     base_data(vendor_name="Caf\u00e9 \u00d1o\u00f1o Ltd",
               legal_name="Caf\u00e9 \u00d1o\u00f1o Ltd",
               bank_holder_name="Caf\u00e9 \u00d1o\u00f1o Ltd",
               bank_proof_text=letter("Caf\u00e9 \u00d1o\u00f1o Ltd")),
     status="approved", reasons=[], actions=[])

# 15. Empty bank-proof letter -> pending. The account cross-check is only
#     emitted when a letter exists, so only bank_proof_document blocks.
case("eval_15_empty_letter",
     base_data(bank_proof_text=""),
     status="pending",
     reasons=["bank_proof_document"],
     actions=["No bank-proof document provided - please attach a bank letter/statement"])

# 16. Punctuation-only account (em dash) -> unreadable -> pending.
case("eval_16_punctuation_account",
     base_data(bank_account_number="\u2014",
               bank_proof_text=letter("Acme Manufacturing Pvt Ltd")),
     status="pending",
     reasons=["bank_account_present"],
     actions=["Bank account number is unreadable — please provide a readable "
              "account number"])


def main():
    for c in CASES:
        (EVAL / f"{c['name']}.json").write_text(
            json.dumps({"name": c["name"], "pdf": c["pdf"], "data": c["data"]},
                       indent=2, ensure_ascii=False))
    expected = {c["name"]: c["expected"] for c in CASES}
    (EVAL / "expected.json").write_text(
        json.dumps(expected, indent=2, ensure_ascii=False))
    print(f"wrote {len(CASES)} fixtures + expected.json to {EVAL}")
    for c in CASES:
        e = c["expected"]
        print(f"  {c['name']:34s} -> {e['status']:8s} "
              f"reasons={e['blocking_reasons']}")


if __name__ == "__main__":
    main()
