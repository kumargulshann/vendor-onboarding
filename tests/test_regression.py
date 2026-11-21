"""
Regression + smoke tests for the vendor-onboarding decision engine.

Runs two ways:
    python3 -m tests.test_regression      # standalone
    python3 -m pytest                     # collected by pytest

Scenarios are plain assertions against the production code path
(parse evidence -> validate -> decide), so they cannot drift from the real
behaviour.
"""
from __future__ import annotations
import json
import sys
import pathlib
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.models import (VendorSubmission, extract_bank_holder,
                        extract_bank_account, normalize_name, normalize_account)
from app.ai_layer import parse_bank_letter
from app.rules import validate
from app.runner import decide, _vendor_messages, _build_bank_proof_summary
import app.runner as _runner

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"

# Isolate run storage so verification does not pollute normal demo history.
_runner.OUT_DIR = pathlib.Path(tempfile.mkdtemp(prefix="asa_tests_"))
_runner.OUT_DIR.mkdir(parents=True, exist_ok=True)
_runner._runs.clear()
_runner._load_saved_runs()


def status(sub: VendorSubmission, evidence: dict | None = None) -> str:
    return decide(validate(sub, evidence))


def make(base: dict, **overrides) -> VendorSubmission:
    d = dict(base)
    d.update(overrides)
    return VendorSubmission.from_dict(d)


def load_fixture(name: str) -> dict:
    return json.loads((DATA / f"{name}.json").read_text())


# A clean, fully-valid baseline submission.
BASE = {
    "vendor_name": "Acme Manufacturing Pvt Ltd",
    "legal_name": "Acme Manufacturing Pvt Ltd",
    "country": "IN", "tax_id": "29ABCDE1234F1Z5",
    "bank_holder_name": "Acme Manufacturing Pvt Ltd",
    "bank_account_number": "1234567890",
    "bank_proof_text": "Account holder: Acme Manufacturing Pvt Ltd\nAccount number: 1234567890",
    "documents": ["incorporation_proof", "bank_proof", "tax_registration", "address_proof"],
}


# --- core decisions -----------------------------------------------------------
def test_happy_path_approved():
    assert status(make(BASE)) == "approved"


def test_missing_bank_holder_is_pending():
    assert status(make(BASE, bank_holder_name=None)) == "pending"


def test_holder_matches_legal_name_when_trading_differs():
    sub = make(BASE, vendor_name="Sunrise Logistics (trading)",
               legal_name="Sunrise Logistics LLC", country="US",
               tax_id="12-3456789", bank_holder_name="Sunrise Logistics LLC",
               bank_account_number="4455667788",
               bank_proof_text="Account holder: Sunrise Logistics LLC\nAccount number: 4455667788")
    assert status(sub) == "approved"


def test_missing_tax_id_pending_malformed_rejected():
    assert status(make(BASE, tax_id=None)) == "pending"
    assert status(make(BASE, tax_id="GT12345")) == "rejected"


# --- bank-proof account cross-check -------------------------------------------
def test_account_mismatch_against_letter_is_pending():
    assert status(make(BASE, bank_account_number="9999999999")) == "pending"


def test_account_match_against_letter_is_approved():
    assert status(make(BASE)) == "approved"


# --- unreadable account (em dash / punctuation) ------------------------------
EM_DASH = "—"
ELLIPSIS = "…"


def test_em_dash_account_is_pending():
    sub = make(BASE, bank_account_number=EM_DASH)
    ev = parse_bank_letter(sub.bank_proof_text)
    rule = validate(sub, ev)
    assert decide(rule) == "pending"
    summary = _build_bank_proof_summary(sub, rule, ev)
    assert summary["account_outcome"] in ("missing", "unreadable")
    msgs = _vendor_messages(sub, rule)
    assert any("readable account number" in m["message"].lower() for m in msgs)
    # only one clear request for the account
    assert sum("account number" in m["message"].lower() for m in msgs) == 1


def test_em_dash_account_through_runner():
    from app.runner import run_process
    run = run_process(make(BASE, bank_account_number=EM_DASH))
    assert run["status"] == "pending"
    assert run["bank_proof_summary"]["account_outcome"] in ("missing", "unreadable")
    assert any("readable account number" in m["message"].lower()
               for m in run["vendor_messages"])
    assert "bank_proof_text" not in run["input"]
    assert "bank_proof_text" not in str(run)


def test_whitespace_and_punctuation_accounts_are_unreadable():
    assert normalize_account("   ") == ""
    assert normalize_account("--") == ""
    assert normalize_account(ELLIPSIS) == ""
    assert status(make(BASE, bank_account_number="   ")) == "pending"
    assert status(make(BASE, bank_account_number="--")) == "pending"


def test_normalize_account_preserves_leading_zeros_and_alphanumerics():
    assert normalize_account("0012-3456") == "00123456"
    assert normalize_account("AB 12-34") == "AB1234"


# --- realistic name comparison (Priority 3) -----------------------------------
def test_comma_in_name_extracted_and_matched():
    # extraction must preserve the comma; comparison is punctuation-insensitive
    letter = "Account holder: Harbor Tools, Inc.\nAccount number: 1234567890"
    assert extract_bank_holder(letter) == "Harbor Tools, Inc."
    sub = make(BASE, legal_name="Harbor Tools, Inc.",
               bank_holder_name="Harbor Tools Inc",
               bank_proof_text=letter)
    assert status(sub) == "approved"


def test_unicode_distinct_names_do_not_match():
    # कमल ट्रेडर्स vs कमला ट्रेडर्स differ by a vowel mark — must NOT match
    assert normalize_name("कमल ट्रेडर्स") != normalize_name("कमला ट्रेडर्स")
    sub = make(BASE, bank_holder_name="कमल ट्रेडर्स",
               bank_proof_text="Account holder: कमला ट्रेडर्स\nAccount number: 1234567890")
    assert status(sub) == "pending"


def test_unicode_legitimate_match():
    # same name written consistently across form and letter -> approved
    sub = make(BASE, legal_name="कमल ट्रेडर्स",
               bank_holder_name="कमल ट्रेडर्स",
               bank_proof_text="Account holder: कमल ट्रेडर्स\nAccount number: 1234567890")
    assert status(sub) == "approved"


def test_empty_comparison_value_is_unreadable():
    sub = make(BASE, legal_name="   ",
               bank_proof_text="Account holder: Acme\nAccount number: 1234567890")
    assert status(sub) == "pending"


def test_missing_bank_proof_document_is_pending():
    assert status(make(BASE, bank_proof_text=None)) == "pending"


# --- one decision per example (Priority 2) ----------------------------------
def test_happy_fixture_approved():
    assert status(make(load_fixture("happy_path"))) == "approved"


def test_tax_fixture_rejected_only_for_tax():
    sub = make(load_fixture("edge_tax_format"))
    assert status(sub) == "rejected"
    msgs = {m["check"] for m in _vendor_messages(sub, validate(sub))}
    assert "tax_id_format" in msgs
    assert "bank_account_match" not in msgs
    assert "documents_complete" not in msgs


def test_bank_mismatch_fixture_pending_only_for_name():
    sub = make(load_fixture("edge_bank_mismatch"))
    assert status(sub) == "pending"
    msgs = {m["check"] for m in _vendor_messages(sub, validate(sub))}
    assert "bank_holder_match" in msgs
    assert "bank_account_match" not in msgs     # account matches the letter
    assert "documents_complete" not in msgs


def test_missing_docs_fixture_pending_and_fixable():
    sub = make(load_fixture("edge_missing_docs"))
    assert status(sub) == "pending"
    # adding the two missing documents -> approved
    fixed = make(sub.to_dict(), documents=["incorporation_proof", "bank_proof",
                                 "tax_registration", "address_proof"])
    assert status(fixed) == "approved"


def test_account_mismatch_fixture_pending_only_for_account():
    sub = make(load_fixture("edge_account_mismatch"))
    assert status(sub) == "pending"
    msgs = {m["check"] for m in _vendor_messages(sub, validate(sub))}
    assert "bank_account_match" in msgs
    assert "bank_holder_match" not in msgs


# --- vendor messaging (Priority 4) -------------------------------------------
def test_missing_docs_message_lists_specifics():
    sub = make(BASE, documents=["incorporation_proof", "bank_proof"])
    msgs = {m["check"]: m["message"] for m in _vendor_messages(sub, validate(sub))}
    assert "address proof" in msgs["documents_complete"]
    assert "tax registration certificate" in msgs["documents_complete"]


def test_blank_holder_gives_single_message():
    sub = make(BASE, bank_holder_name=None)
    rule = validate(sub)
    msgs = _vendor_messages(sub, rule)
    checks = {m["check"] for m in msgs}
    assert "bank_holder_present" in checks
    assert "bank_holder_match" not in checks


def test_independent_issues_all_reported():
    # missing holder AND conflicting account -> both corrections are given
    sub = make(BASE, bank_holder_name=None, bank_account_number="9999999999")
    rule = validate(sub)
    checks = {m["check"] for m in _vendor_messages(sub, rule)}
    assert "bank_holder_present" in checks
    assert "bank_account_match" in checks


# --- evidence summary states (Priority 4) ------------------------------------
def test_summary_account_outcome_missing_when_no_form_account():
    sub = make(BASE, bank_account_number="")
    evidence = parse_bank_letter(sub.bank_proof_text)
    rule = validate(sub, evidence)
    summary = _build_bank_proof_summary(sub, rule, evidence)
    assert summary["account_outcome"] == "missing"


def test_summary_account_outcome_match_and_mismatch():
    ok = make(BASE)
    ev = parse_bank_letter(ok.bank_proof_text)
    assert _build_bank_proof_summary(ok, validate(ok, ev), ev)["account_outcome"] == "match"
    bad = make(BASE, bank_account_number="9999999999")
    ev2 = parse_bank_letter(bad.bank_proof_text)
    assert _build_bank_proof_summary(bad, validate(bad, ev2), ev2)["account_outcome"] == "mismatch"


# --- extraction helpers -------------------------------------------------------
def test_extract_account_number():
    assert extract_bank_account("Account no: AB123456789") == "AB123456789"


def test_evidence_is_shared_not_double_parsed():
    # the holder used by the rules equals the holder in the summary
    sub = make(BASE)
    evidence = parse_bank_letter(sub.bank_proof_text)
    rule = validate(sub, evidence)
    summary = _build_bank_proof_summary(sub, rule, evidence)
    assert summary["holder_extracted"] == evidence["holder"]


# --- Day 2: PDF evidence + history 404 -------------------------------------
PDF_DIR = DATA / "pdf"
ACCOUNT_FULL = "1234567890"


def pdf_bytes(name):
    return (PDF_DIR / name).read_bytes()


def pdf_submission(fixture, pdf_name):
    """Build a submission whose bank evidence is the given PDF's extracted text."""
    from app.pdf_evidence import extract_pdf_text
    ev = extract_pdf_text(pdf_bytes(pdf_name))
    sub = make(load_fixture(fixture))
    sub.bank_proof_text = ev["text"]
    sub.bank_evidence_source = "pdf"
    return sub, ev


def test_pdf_two_page_match_approved():
    sub, ev = pdf_submission("happy_path", "pdf_two_page_match.pdf")
    assert ev["pages"] == 2 and ev["readable"]
    assert status(sub, parse_bank_letter(sub.bank_proof_text)) == "approved"


def test_pdf_account_mismatch_pending():
    sub, ev = pdf_submission("happy_path", "pdf_account_mismatch.pdf")
    assert status(sub, parse_bank_letter(sub.bank_proof_text)) == "pending"
    summary_rule = validate(sub, parse_bank_letter(sub.bank_proof_text))
    summ = _build_bank_proof_summary(sub, summary_rule, parse_bank_letter(sub.bank_proof_text))
    assert summ["account_outcome"] == "mismatch"
    assert summ["source"] == "pdf"


def test_pdf_image_only_after_happy_example_is_pending():
    # The happy-path example text must NEVER approve an image-only PDF upload.
    sub = make(load_fixture("happy_path"))
    sub.bank_evidence_source = "pdf"
    sub.bank_proof_text = ""
    assert status(sub, parse_bank_letter("")) == "pending"
    rule = validate(sub, parse_bank_letter(""))
    summ = _build_bank_proof_summary(sub, rule, parse_bank_letter(""))
    assert summ["name_outcome"] == "unreadable"
    assert summ["source"] == "pdf"


def test_invalid_pdf_raises_clear_error():
    from app.pdf_evidence import extract_pdf_text, PdfEvidenceError
    try:
        extract_pdf_text(pdf_bytes("invalid.pdf"))
        assert False, "expected PdfEvidenceError for invalid PDF"
    except PdfEvidenceError as e:
        assert "valid PDF" in e.message


def test_api_404_for_unknown_run():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.get("/api/runs/does-not-exist")
    assert r.status_code == 404


def test_pdf_run_record_hides_text_and_full_account():
    from app.runner import run_process
    sub, ev = pdf_submission("happy_path", "pdf_two_page_match.pdf")
    run = run_process(sub)
    blob = json.dumps(run)
    assert "BANK OF TEST" not in blob          # raw document text hidden
    assert ACCOUNT_FULL not in blob           # full account hidden
    assert "bank_proof_text" not in blob      # raw letter key absent
    assert run["bank_proof_summary"]["source"] == "pdf"
    assert run["bank_proof_summary"]["holder_extracted"] == "Acme Manufacturing Pvt Ltd"


# --- API smoke test (skipped if test client deps unavailable) -----------------
def test_api_smoke():
    try:
        from fastapi.testclient import TestClient
    except Exception:
        return  # test client deps not available; skip
    from app.main import app
    client = TestClient(app)
    r = client.post("/api/run", json={"data": BASE})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    rid = r.json()["id"]
    full = client.get(f"/api/runs/{rid}").json()
    assert "bank_proof_text" not in full["input"]
    assert "bank_proof_text" not in str(full)
    assert full["bank_proof_summary"]["holder_extracted"] == "Acme Manufacturing Pvt Ltd"



# --- Day 3: correction & resubmit, multi-attempt cases ----------------------
def test_new_submission_starts_a_case():
    from app.runner import run_process
    r = run_process(make(BASE))
    assert r["case_id"] and len(r["case_id"]) == 8
    assert r["attempt_number"] == 1
    assert r["previous_run_id"] is None
    assert r["created_at"]


def test_correction_appends_to_same_case():
    from app.runner import run_process
    r1 = run_process(make(BASE, bank_account_number="9999999999"))  # pending
    # correct the account number so the comparison passes
    fixed = make(BASE, bank_account_number="1234567890")
    fixed.case_id = r1["case_id"]
    fixed.expected_previous_run_id = r1["id"]
    r2 = run_process(fixed)
    assert r2["case_id"] == r1["case_id"]            # same case
    assert r2["attempt_number"] == 2
    assert r2["previous_run_id"] == r1["id"]          # linked to prior attempt
    assert r2["status"] == "approved"


def test_two_corrections_make_attempt_three():
    from app.runner import run_process
    r1 = run_process(make(BASE, bank_account_number="9999999999"))  # pending
    bad = make(BASE, bank_account_number="1111111111")  # still a mismatch
    bad.case_id = r1["case_id"]
    bad.expected_previous_run_id = r1["id"]
    r2 = run_process(bad)
    assert r2["attempt_number"] == 2 and r2["status"] == "pending"
    good = make(BASE)
    good.case_id = r1["case_id"]
    good.expected_previous_run_id = r2["id"]
    r3 = run_process(good)
    assert r3["attempt_number"] == 3
    assert r3["status"] == "approved"
    assert r3["case_id"] == r1["case_id"]
    assert r3["previous_run_id"] == r2["id"]


def test_invalid_correction_targets_create_no_attempts_over_json_and_pdf():
    from fastapi.testclient import TestClient
    from app.main import app

    pending = make(BASE, bank_account_number="9999999999")
    first = _runner.run_process(pending)
    second = _runner.run_process(pending)
    before = _runner.list_runs()
    files_before = sorted(_runner.OUT_DIR.glob("*.json"))

    with TestClient(app) as client:
        for target in (
            {"case_id": "unknown-case"},
            {"expected_previous_run_id": first["id"]},
            {"case_id": "unknown-case", "expected_previous_run_id": first["id"]},
            {"case_id": first["case_id"], "expected_previous_run_id": second["id"]},
        ):
            result = client.post("/api/run", json={"data": BASE | target})
            assert result.status_code == 409
            assert result.json()["error"]

            result = client.post(
                "/api/run/stream",
                data={k: v for k, v in (BASE | target).items() if k != "bank_proof_text"},
                files={"file": ("bank.pdf", pdf_bytes("pdf_two_page_match.pdf"),
                                "application/pdf")},
            )
            assert "event: error" in result.text
            assert "event: result" not in result.text

    assert _runner.list_runs() == before
    assert sorted(_runner.OUT_DIR.glob("*.json")) == files_before


def test_failed_correction_write_keeps_previous_attempt_current():
    from unittest import TestCase
    from unittest.mock import patch

    first = _runner.run_process(make(BASE, bank_account_number="9999999999"))
    corrected = make(BASE, case_id=first["case_id"], expected_previous_run_id=first["id"])
    with patch.object(pathlib.Path, "replace", side_effect=OSError("Write failed")):
        with TestCase().assertRaises(OSError):
            _runner.run_process(corrected)

    assert _runner.get_case(first["case_id"])["attempt_count"] == 1
    assert _runner.get_run(first["id"])["correction_allowed"] is True
    assert not list(_runner.OUT_DIR.glob("*.tmp"))

    second = _runner.run_process(corrected)
    saved = json.loads((_runner.OUT_DIR / f"{second['id']}.json").read_text(encoding="utf-8"))
    assert saved["attempt_number"] == 2
    assert saved["comparison"]["previous_run_id"] == first["id"]


def test_list_and_get_case_group_attempts():
    from app.runner import run_process, list_cases, get_case
    r1 = run_process(make(BASE, bank_account_number="9999999999"))  # pending
    r2 = run_process(make(BASE, case_id=r1["case_id"], expected_previous_run_id=r1["id"]))             # approved
    cases = list_cases()
    match = [c for c in cases if c["case_id"] == r1["case_id"]]
    assert len(match) == 1
    assert match[0]["attempt_count"] == 2
    assert match[0]["latest_status"] == "approved"
    case = get_case(r1["case_id"])
    assert case["attempt_count"] == 2
    assert [a["attempt_number"] for a in case["attempts"]] == [1, 2]
    assert case["attempts"][0]["status"] == "pending"
    assert case["attempts"][1]["status"] == "approved"


def test_get_case_404_for_unknown():
    from app.runner import get_case
    assert get_case("nope1234") is None


def test_legacy_run_without_case_id_is_own_case():
    # A record that predates case IDs must still be readable as a single case.
    from app.runner import run_process, list_cases, get_case
    run = run_process(make(BASE))
    legacy = {"id": "legacy01", "status": "approved",
              "input": {"vendor_name": "Legacy Vendor"},
              "reasoning": "old", "attempt_number": 1}
    import app.runner as rr
    with rr._lock:
        rr._runs.append(legacy)
    case = get_case("legacy01")
    assert case is not None and case["attempt_count"] == 1
    assert case["vendor"] == "Legacy Vendor"


def test_correction_record_hides_full_account_and_letter():
    from app.runner import run_process
    r1 = run_process(make(BASE, bank_account_number="9999999999"))
    fixed = make(BASE)
    fixed.case_id = r1["case_id"]
    fixed.expected_previous_run_id = r1["id"]
    r2 = run_process(fixed)
    blob = json.dumps(r2)
    assert "BANK OF TEST" not in blob
    assert "1234567890" not in blob          # full account never stored
    assert "bank_proof_text" not in blob


def test_correction_runs_same_rules_rejects_malformed_taxid():
    # A correction still goes through the full evidence/decision rules.
    from app.runner import run_process
    r1 = run_process(make(BASE, bank_account_number="9999999999"))
    bad = make(BASE, bank_account_number="1234567890", tax_id="12345")
    bad.case_id = r1["case_id"]
    bad.expected_previous_run_id = r1["id"]
    r2 = run_process(bad)
    assert r2["attempt_number"] == 2
    assert r2["status"] == "rejected"       # malformed tax id still blocks


def test_double_submit_creates_single_attempt():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    first = _runner.run_process(make(BASE, bank_account_number="9999999999"))
    ready = Barrier(2)

    def submit_correction():
        correction = make(BASE, case_id=first["case_id"],
                          expected_previous_run_id=first["id"])
        ready.wait(timeout=5)
        try:
            return _runner.run_process(correction)
        except _runner.CorrectionConflict as conflict:
            return conflict

    with ThreadPoolExecutor(max_workers=2) as executor:
        submissions = [executor.submit(submit_correction) for _ in range(2)]
        outcomes = [submission.result(timeout=10) for submission in submissions]
    saved = [outcome for outcome in outcomes if isinstance(outcome, dict)]
    rejected = [outcome for outcome in outcomes if isinstance(outcome, _runner.CorrectionConflict)]
    assert len(saved) == len(rejected) == 1
    assert saved[0]["attempt_number"] == 2
    assert rejected[0].latest_run_id == saved[0]["id"]
    assert _runner.get_case(first["case_id"])["attempt_count"] == 2


def test_api_correction_appends_via_multipart():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    # attempt 1: text submission with a conflicting account -> pending
    r1 = c.post("/api/run", json={"data": make(BASE, bank_account_number="9999999999").to_dict()})
    assert r1.status_code == 200
    att1 = r1.json()
    assert att1["status"] == "pending" and att1["attempt_number"] == 1
    # attempt 2: multipart correction, same case, correct account
    files = {"file": ("bank.pdf", pdf_bytes("pdf_two_page_match.pdf"), "application/pdf")}
    form = {"vendor_name": BASE["vendor_name"], "legal_name": BASE["legal_name"],
            "country": "IN", "tax_id": BASE["tax_id"],
            "bank_holder_name": BASE["bank_holder_name"],
            "bank_account_number": "1234567890",
            "documents": BASE["documents"],
            "case_id": att1["case_id"],
            "expected_previous_run_id": att1["id"]}
    r2 = c.post("/api/run", files=files, data=form)
    assert r2.status_code == 200
    att2 = r2.json()
    assert att2["case_id"] == att1["case_id"]
    assert att2["attempt_number"] == 2
    assert att2["status"] == "approved"
    assert att2["bank_proof_summary"]["source"] == "pdf"
    blob = json.dumps(att2)
    assert "BANK OF TEST" not in blob and "1234567890" not in blob
    # case view groups both attempts
    case = c.get("/api/cases/" + att1["case_id"]).json()
    assert case["attempt_count"] == 2
    assert [a["attempt_number"] for a in case["attempts"]] == [1, 2]


def test_api_bad_pdf_stream_emits_single_error():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    files = {"file": ("bad.pdf", pdf_bytes("invalid.pdf"), "application/pdf")}
    r = c.post("/api/run/stream", files=files, data={"vendor_name": "X"})
    assert r.status_code == 200
    body = r.text
    assert "event: error" in body
    assert "event: result" not in body
    assert "BANK OF TEST" not in body




# --- Day 4: reviewer comparison, upload guards, checklist restore -----------
def test_comparison_account_correction_summary():
    from app.runner import run_process
    r1 = run_process(make(BASE, bank_account_number="9999999999"))   # pending
    assert r1["comparison"] is None                              # first attempt standalone
    r2 = run_process(make(BASE, case_id=r1["case_id"], expected_previous_run_id=r1["id"]))            # approved
    comp = r2["comparison"]
    assert comp is not None
    assert comp["previous_status"] == "pending"
    assert comp["current_status"] == "approved"
    assert "Attempt 2 was approved." in comp["summary"]
    assert "Resolved since attempt 1: the submitted account matched the new evidence." in comp["summary"]
    assert "Attempt 1 at" in comp["summary"] and "attempt 2 at" in comp["summary"]
    changed = {c["name"]: (c["before"], c["after"]) for c in comp["changed_checks"]}
    assert changed["bank_account_match"] == (False, True)


def test_comparison_missing_docs_corrected():
    from app.runner import run_process
    docs2 = ["incorporation_proof", "bank_proof"]   # missing tax + address
    r1 = run_process(make(BASE, documents=docs2))
    assert r1["status"] == "pending"
    assert any("documents" in m["check"] for m in r1["vendor_messages"])
    r2 = run_process(make(BASE, case_id=r1["case_id"], expected_previous_run_id=r1["id"]))
    comp = r2["comparison"]
    assert comp is not None
    assert any(c["name"] == "documents_complete" and c["after"]
               for c in comp["changed_checks"])
    assert "all required documents were provided" in comp["summary"]


def test_comparison_incomplete_correction_stays_pending():
    from app.runner import run_process
    r1 = run_process(make(BASE, bank_account_number="9999999999"))  # pending
    r2 = run_process(make(BASE, bank_account_number="1111111111",
                          case_id=r1["case_id"], expected_previous_run_id=r1["id"]))                    # still pending
    comp = r2["comparison"]
    assert comp is not None
    assert comp["current_status"] == "pending"
    # leads with the current decision and the remaining blocker
    assert "Attempt 2 is still held because" in comp["summary"]
    assert "the submitted account differed from the bank letter" in comp["summary"]
    assert "current_blockers" in comp and comp["current_blockers"]


def test_comparison_uses_masked_evidence_only():
    from app.runner import run_process
    r1 = run_process(make(BASE, bank_account_number="9999999999"))
    r2 = run_process(make(BASE, case_id=r1["case_id"], expected_previous_run_id=r1["id"]))
    blob = json.dumps(r2["comparison"])
    assert "BANK OF TEST" not in blob
    assert "1234567890" not in blob          # full account never in comparison
    assert "9999999999" not in blob


def test_oversized_pdf_bounded_read_rejected():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    # build a PDF padded beyond the 1 MB limit
    from app.pdf_evidence import MAX_PDF_BYTES
    pad = MAX_PDF_BYTES + 4096
    files = {"file": ("big.pdf", b"%PDF-1.4\n" + b"0" * pad, "application/pdf")}
    r = c.post("/api/run", files=files, data={"vendor_name": "X"})
    assert r.status_code == 400
    assert "too large" in r.json()["error"].lower()


def test_encrypted_pdf_message_asks_unprotected():
    from app.pdf_evidence import extract_pdf_text, PdfEvidenceError
    # build a password-protected PDF with pypdf
    from pypdf import PdfWriter, PdfReader
    from io import BytesIO
    reader = PdfReader(BytesIO(pdf_bytes("pdf_two_page_match.pdf")))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("secret")
    buf = BytesIO()
    writer.write(buf)
    try:
        extract_pdf_text(buf.getvalue())
        assert False, "expected PdfEvidenceError for encrypted PDF"
    except PdfEvidenceError as e:
        assert "unprotected" in e.message.lower()
        assert "password" in e.message.lower()


def test_api_encrypted_pdf_returns_clear_400():
    from fastapi.testclient import TestClient
    from app.main import app
    from pypdf import PdfWriter, PdfReader
    from io import BytesIO
    reader = PdfReader(BytesIO(pdf_bytes("pdf_two_page_match.pdf")))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("secret")
    buf = BytesIO(); writer.write(buf)
    c = TestClient(app)
    files = {"file": ("enc.pdf", buf.getvalue(), "application/pdf")}
    r = c.post("/api/run", files=files, data={"vendor_name": "X"})
    assert r.status_code == 400
    assert "unprotected" in r.json()["error"].lower()


def test_correction_restores_original_document_checklist():
    # The saved attempt's documents are what a correction should restore.
    from app.runner import run_process
    inp = make(BASE, documents=["incorporation_proof", "bank_proof"])
    r = run_process(inp)
    saved = r["input"]["documents"]
    assert set(saved) == {"incorporation_proof", "bank_proof"}
    # a correction built from this attempt should carry those docs back
    # (the runner records them; the UI restores them from r.input.documents)
    assert "documents" in r["input"]


def test_new_submission_after_correction_gets_new_case():
    from app.runner import run_process
    r1 = run_process(make(BASE, bank_account_number="9999999999"))
    r2 = run_process(make(BASE, case_id=r1["case_id"], expected_previous_run_id=r1["id"]))          # correction
    assert r2["case_id"] == r1["case_id"]
    # a fresh submission (no case_id) must NOT append to the old case
    r3 = run_process(make(BASE))
    assert r3["case_id"] != r1["case_id"]
    assert r3["attempt_number"] == 1


def test_comparison_present_in_saved_run_record():
    from app.runner import run_process
    r1 = run_process(make(BASE, bank_account_number="9999999999"))
    r2 = run_process(make(BASE, case_id=r1["case_id"], expected_previous_run_id=r1["id"]))
    # simulate a restart: the record on disk must carry the comparison
    import app.runner as rr
    reloaded = rr.get_run(r2["id"])
    assert reloaded is not None
    assert reloaded["comparison"] is not None
    assert "matched the new evidence" in reloaded["comparison"]["summary"]




# --- Day 5: close the three Day-4 findings ----------------------------------
def test_comparison_leads_with_current_blocker_when_mixed():
    # Documents fixed + new account mismatch introduced -> summary must lead
    # with the CURRENT decision/blocker, then explain what was resolved.
    from app.runner import run_process
    docs2 = ["incorporation_proof", "bank_proof"]   # missing tax + address
    r1 = run_process(make(BASE, documents=docs2, bank_account_number="1234567890"))
    assert r1["status"] == "pending"
    # correct: supply missing docs but introduce an account mismatch
    r2 = run_process(make(BASE, bank_account_number="9999999999",
                          case_id=r1["case_id"], expected_previous_run_id=r1["id"]))
    assert r2["status"] == "pending"
    comp = r2["comparison"]
    assert comp is not None
    # leads with current blocker, then resolved
    assert "Attempt 2 is still held because" in comp["summary"]
    assert "the submitted account differed from the bank letter" in comp["summary"]
    assert "all required documents were provided" in comp["summary"]
    assert comp["current_blockers"], "current blockers must be listed"


def test_stale_correction_creates_zero_attempts():
    # A correction opened against an old attempt must NOT append once a newer
    # attempt exists. It raises CorrectionConflict and the case history is
    # left untouched (still exactly 2 attempts, latest decision unchanged).
    from app.runner import run_process, get_case, CorrectionConflict
    r1 = run_process(make(BASE, bank_account_number="9999999999"))   # attempt 1 pending
    r2 = run_process(make(BASE, case_id=r1["case_id"],
                          expected_previous_run_id=r1["id"]))        # attempt 2 approved
    assert get_case(r1["case_id"])["attempt_count"] == 2
    # tab B still holds the form for attempt 1; submitting it now is stale
    try:
        run_process(make(BASE, case_id=r1["case_id"],
                          expected_previous_run_id=r1["id"]))
        assert False, "stale correction must raise CorrectionConflict"
    except CorrectionConflict as e:
        assert e.latest_run_id == r2["id"]
        assert e.latest_attempt_number == 2
    # history preserved: still exactly 2 attempts, latest decision unchanged
    case = get_case(r1["case_id"])
    assert case["attempt_count"] == 2
    assert case["latest_status"] == "approved"


def test_correction_to_closed_case_creates_zero_attempts():
    # Correcting a case whose latest attempt is approved (closed) is rejected.
    from app.runner import run_process, get_case, CorrectionConflict
    r1 = run_process(make(BASE, bank_account_number="9999999999"))   # attempt 1 pending
    r2 = run_process(make(BASE, case_id=r1["case_id"],
                          expected_previous_run_id=r1["id"]))        # attempt 2 approved
    try:
        run_process(make(BASE, bank_account_number="1111111111",
                          case_id=r1["case_id"],
                          expected_previous_run_id=r2["id"]))
        assert False, "correction to a closed case must raise"
    except CorrectionConflict as e:
        assert e.latest_run_id == r2["id"]
    assert get_case(r1["case_id"])["attempt_count"] == 2


def test_unknown_correction_target_creates_zero_attempts():
    # A correction whose expected predecessor does not exist is rejected and
    # creates zero attempts.
    from app.runner import run_process, get_case, CorrectionConflict
    r1 = run_process(make(BASE, bank_account_number="9999999999"))
    try:
        run_process(make(BASE, case_id=r1["case_id"],
                          expected_previous_run_id="does-not-exist"))
        assert False, "unknown correction target must raise"
    except CorrectionConflict:
        pass
    assert get_case(r1["case_id"])["attempt_count"] == 1


def test_valid_correction_appends_with_verified_predecessor():
    # A correction whose declared predecessor is still the latest pending
    # attempt appends normally and links to the verified predecessor.
    from app.runner import run_process
    r1 = run_process(make(BASE, bank_account_number="9999999999"))   # attempt 1 pending
    r2 = run_process(make(BASE, case_id=r1["case_id"],
                          expected_previous_run_id=r1["id"]))
    assert r2["attempt_number"] == 2
    assert r2["previous_run_id"] == r1["id"]
    assert r2["predecessor_stale"] is False
    assert r2["comparison"]["previous_run_id"] == r1["id"]


def test_new_case_path_still_works():
    # A genuinely new submission (no case_id, no predecessor) always starts a
    # fresh case with one attempt.
    from app.runner import run_process
    r = run_process(make(BASE, bank_account_number="9999999999"))
    assert r["attempt_number"] == 1
    assert r["previous_run_id"] is None
    assert r["predecessor_stale"] is False


def test_predecessor_match_not_flagged():
    from app.runner import run_process
    r1 = run_process(make(BASE, bank_account_number="9999999999"))
    r2 = run_process(make(BASE, case_id=r1["case_id"],
                          expected_previous_run_id=r1["id"]))
    assert r2["predecessor_stale"] is False
    assert r2["previous_run_id"] == r1["id"]


def test_only_latest_pending_attempt_is_correctable():
    from app.runner import run_process, get_run
    # attempt 1 pending -> correctable
    r1 = run_process(make(BASE, bank_account_number="9999999999"))
    assert get_run(r1["id"])["correction_allowed"] is True
    # attempt 2 also pending (still a blocker) -> becomes latest, correctable;
    # attempt 1 is now historical and not correctable
    r2 = run_process(make(BASE, bank_account_number="1111111111",
                          case_id=r1["case_id"],
                          expected_previous_run_id=r1["id"]))
    assert get_run(r2["id"])["correction_allowed"] is True
    assert get_run(r1["id"])["correction_allowed"] is False
    # attempt 3 approved -> case closed; attempt 3 (latest) not correctable
    r3 = run_process(make(BASE, case_id=r1["case_id"],
                          expected_previous_run_id=r2["id"]))
    assert r3["status"] == "approved"
    assert get_run(r3["id"])["correction_allowed"] is False
    assert get_run(r2["id"])["correction_allowed"] is False


def test_new_submission_has_no_case_link():
    # A fresh submission (no case_id, no expected predecessor) starts a new
    # case and is correctable when pending — no inherited linkage.
    from app.runner import run_process
    r = run_process(make(BASE, bank_account_number="9999999999"))
    assert r["previous_run_id"] is None
    assert r["predecessor_stale"] is False
    assert r["correction_allowed"] is True


def test_vendor_actions_not_duplicated_in_run():
    # vendor_messages and needed_back must be the same set (UI shows once).
    from app.runner import run_process
    r = run_process(make(BASE, documents=["incorporation_proof"]))
    msgs = [m["message"] for m in r["vendor_messages"]]
    assert r["needed_back"] == msgs




def test_holder_conflict_also_flags_bank_proof_document():
    # The bank-proof document check compares the LETTER holder to the legal
    # name, so a holder conflict fails both checks (correct behaviour).
    from app.runner import run_process
    r = run_process(make(BASE, legal_name="Meridian Company Ltd",
                         vendor_name="Meridian Company Ltd",
                         bank_holder_name="Meridian Co",
                         bank_proof_text="Account holder: Meridian Co\n"
                                         "Account number: 1234567890"))
    assert r["status"] == "pending"
    reasons = {c["name"] for c in r["checks"]
               if not c["passed"] and c.get("severity") == "error"}
    assert "bank_holder_match" in reasons
    assert "bank_proof_document" in reasons


def test_unsupported_country_also_flags_tax_id():
    from app.runner import run_process
    r = run_process(make(BASE, country="FR"))
    assert r["status"] == "rejected"
    reasons = {c["name"] for c in r["checks"]
               if not c["passed"] and c.get("severity") == "error"}
    assert "country_supported" in reasons
    assert "tax_id_format" in reasons


def test_empty_letter_does_not_emit_account_match():
    # With no letter text the account cross-check is not emitted; only the
    # missing bank-proof check blocks.
    from app.runner import run_process
    r = run_process(make(BASE, bank_proof_text=""))
    assert r["status"] == "pending"
    check_names = {c["name"] for c in r["checks"]}
    assert "bank_proof_document" in check_names
    assert "bank_account_match" not in check_names


def test_evaluation_fixtures_exist():
    import pathlib
    eval_dir = pathlib.Path(__file__).resolve().parent.parent / "data" / "eval"
    fixtures = sorted(eval_dir.glob("eval_*.json"))
    fixtures = [f for f in fixtures if f.stem != "expected"]
    assert len(fixtures) == 16
    expected = __import__("json").loads((eval_dir / "expected.json").read_text())
    assert len(expected) == 16



# --- standalone runner --------------------------------------------------------
def _run_all():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"[PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)


if __name__ == "__main__":
    _run_all()
