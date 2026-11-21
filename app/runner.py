"""
The process runner: takes a submission, runs it through the stages, and
produces a clear status decision with everything that happened in between.

Status model (matches PS-2):
  - approved  : all mandatory checks pass
  - pending   : nothing is *fatally* wrong, but something is missing/needed
  - rejected  : a hard inconsistency or unverifiable input

Each run is recorded so the dashboard can show history, status and outputs.
The full bank letter is NEVER persisted — we store only the extracted holder,
masked account endings, comparison outcome and reason, so run records are safe
to share publicly.
"""
from __future__ import annotations
import json
import os
import tempfile
import time
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from .models import VendorSubmission, DOCUMENT_LABELS, mask_ending, normalize_account
from .rules import validate
from .ai_layer import reason, parse_bank_letter

BASE = Path(__file__).resolve().parent.parent
DEFAULT_RUN_DIR = (
    Path(tempfile.gettempdir()) / "vendor-onboarding-runs"
    if os.environ.get("VERCEL") == "1"
    else BASE / "outputs"
)
OUT_DIR = Path(os.environ.get("VENDOR_ONBOARDING_RUN_DIR") or DEFAULT_RUN_DIR)
OUT_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()
_runs: list[dict] = []


class CorrectionConflict(Exception):
    """
    Raised when a correction cannot be saved because its target is stale, the
    case is closed, or the correction target is unknown. Raising means ZERO
    attempts are created — the case history is left untouched. Carries the
    latest attempt so the reviewer can be directed to it.
    """
    def __init__(self, message, case_id=None, latest_run_id=None,
                 latest_attempt_number=None):
        self.message = message
        self.case_id = case_id
        self.latest_run_id = latest_run_id
        self.latest_attempt_number = latest_attempt_number
        super().__init__(message)


def _mask_account(value: str | None) -> str:
    return mask_ending(value)


def _mask_input(d: dict) -> dict:
    """Return a copy of the submission with bank-sensitive fields masked and the
    full bank letter removed (replaced by a safe summary)."""
    m = dict(d)
    m["bank_account_number"] = _mask_account(m.get("bank_account_number"))
    m["bank_routing"] = _mask_account(m.get("bank_routing"))
    # remove the raw letter; it is summarised separately
    m.pop("bank_proof_text", None)
    return m


def _build_bank_proof_summary(sub: VendorSubmission, rule, evidence: dict) -> dict:
    """
    A shareable summary of the bank-proof check (no raw letter). Uses the SAME
    evidence the rules used, so the displayed result matches the decision.
    Outcomes distinguish missing evidence, conflicting evidence, and confirmed
    match.
    """
    letter_holder = evidence.get("holder")
    letter_acc = evidence.get("account") or ""
    raw_form_acc = sub.bank_account_number or ""
    form_acc_norm = normalize_account(raw_form_acc)
    by_name = next((c for c in rule.checks if c.name == "bank_proof_document"), None)
    by_acc = next((c for c in rule.checks if c.name == "bank_account_match"), None)

    if not form_acc_norm:
        # no readable account to compare — distinguish truly empty vs punctuation
        account_outcome = "missing" if not raw_form_acc.strip() else "unreadable"
    elif not letter_acc:
        account_outcome = "not comparable"   # form has one, letter doesn't
    elif by_acc and by_acc.passed:
        account_outcome = "match"
    else:
        account_outcome = "mismatch"

    source = (sub.bank_evidence_source or "text").lower()
    if not sub.bank_proof_text:
        name_outcome = "unreadable" if source == "pdf" else "missing"
    elif not letter_holder:
        name_outcome = "unreadable"
    elif by_name and by_name.passed:
        name_outcome = "match"
    else:
        name_outcome = "mismatch"

    return {
        "source": source,
        "holder_extracted": letter_holder,
        "form_account_masked": _mask_account(raw_form_acc),
        "letter_account_masked": _mask_account(letter_acc),
        "name_outcome": name_outcome,
        "account_outcome": account_outcome,
        "reason": by_name.detail if by_name else "No bank-proof document provided.",
    }


def _vendor_messages(sub: VendorSubmission, rule) -> list[dict]:
    """
    Build ONE clear, actionable instruction per distinct issue. Dependent
    checks are skipped when their prerequisite already failed, so a vendor is
    not told to fix a mismatch when the name was never provided.
    """
    messages: list[dict] = []
    failed = {c.name: c for c in rule.errors}

    # missing docs -> list the specific documents, once
    if "documents_complete" in failed:
        detail = failed["documents_complete"].detail
        missing = []
        if ":" in detail:
            tail = detail.split(":", 1)[1].strip()
            missing = [x.strip() for x in tail.split(",") if x.strip()]
        names = [DOCUMENT_LABELS.get(m, m) for m in missing]
        text = "Please provide your " + " and ".join(names) + "."
        messages.append({"check": "documents_complete", "message": text})

    order = ["vendor_name_present", "country_supported", "tax_id_format",
             "bank_holder_present", "bank_account_present", "bank_holder_match",
             "bank_proof_document", "bank_account_match"]

    for name in order:
        if name not in failed:
            continue
        # suppress a check only when its OWN prerequisite is missing
        if name == "bank_holder_match" and "bank_holder_present" in failed:
            continue          # no holder name to compare -> already asked for it
        if name == "bank_account_match" and "bank_account_present" in failed:
            continue          # no account number to compare -> already asked for it
        c = failed[name]
        messages.append({"check": name, "message": c.detail})

    return messages


def decide(rule) -> str:
    """Single source of truth for status selection (used by the runner + tests)."""
    if rule.passed:
        return "approved"
    if rule.reject_errors:
        return "rejected"      # hard/unverifiable signals
    return "pending"           # missing or needs vendor action


def _load_saved_runs() -> None:
    """Load run summaries from disk so history survives a restart."""
    loaded = []
    for p in sorted(OUT_DIR.glob("*.json")):
        try:
            loaded.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    with _lock:
        _runs.clear()
        _runs.extend(loaded)


def _persist(run: dict) -> None:
    destination = OUT_DIR / f"{run['id']}.json"
    output = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=OUT_DIR, suffix=".tmp", delete=False,
    )
    temporary = Path(output.name)
    try:
        with output:
            json.dump(run, output, indent=2, default=str)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _finalize_and_append(run: dict, submission: VendorSubmission) -> dict:
    """
    Atomically verify the correction target (if this is a correction) and
    append the run - the check and the append happen under ONE lock acquisition
    so no other save can slip in between. A stale correction, a correction to
    a closed case, or an unknown/mismatched target raises CorrectionConflict and
    creates ZERO attempts.

    On success, sets the verified attempt_number, previous_run_id and builds the
    comparison against the verified predecessor.
    """
    case_id = submission.case_id
    expected = submission.expected_previous_run_id
    run["comparison"] = None   # set for first attempts; replaced below if corrected
    with _lock:
        if case_id is None and expected is None:
            run["case_id"] = uuid.uuid4().hex[:8]
            run["previous_run_id"] = None
            run["attempt_number"] = 1
        else:
            if not case_id or not expected:
                raise CorrectionConflict(
                    "A correction requires both a case and the attempt being "
                    "corrected. Reopen the latest attempt before submitting.",
                    case_id=case_id)
            case_runs = [r for r in _runs if r.get("case_id") == case_id]
            if not case_runs:
                raise CorrectionConflict(
                    "This case does not exist. Reopen the intended case before "
                    "submitting a correction.", case_id=case_id)
            latest = max(case_runs, key=lambda r: r.get("attempt_number", 1))
            recovery = {"case_id": case_id, "latest_run_id": latest["id"],
                        "latest_attempt_number": latest.get("attempt_number", 1)}
            expected_run = next((r for r in case_runs if r["id"] == expected), None)
            if expected_run is None:
                raise CorrectionConflict(
                    "The selected attempt does not belong to this case. Open "
                    "the latest attempt for this case before correcting it.",
                    **recovery)
            if expected_run["id"] != latest["id"]:
                raise CorrectionConflict(
                    f"This correction was opened against attempt "
                    f"{expected_run.get('attempt_number', '?')} "
                    f"but attempt {latest.get('attempt_number')} is now the latest "
                    f"attempt for this case. Open the latest attempt to correct it.",
                    **recovery)
            if latest.get("status") != "pending":
                raise CorrectionConflict(
                    f"This case is closed: its latest attempt "
                    f"({latest.get('attempt_number')}) is "
                    f"{latest.get('status')}. It cannot be corrected.",
                    **recovery)
            run["case_id"] = case_id
            run["previous_run_id"] = latest["id"]
            run["attempt_number"] = latest.get("attempt_number", 1) + 1
            run["comparison"] = build_comparison(run, latest)
        run["correction_allowed"] = run["status"] == "pending"
        run["expected_previous_run_id"] = expected
        _persist(run)
        _runs.append(run)
    return run


def run_process(submission: VendorSubmission, run_id: str | None = None,
                on_event: Optional[Callable[[dict], None]] = None) -> dict:
    run_id = run_id or uuid.uuid4().hex[:8]
    started = time.time()
    stages: list[dict] = []

    def stage(name: str, detail: str, done: bool = True):
        ev = {"stage": name, "detail": detail, "done": done,
              "ts": round(time.time() - started, 3)}
        stages.append(ev)
        if on_event:
            on_event(ev)

    # Stage 1 — accept input
    stage("input_received", "Vendor submission accepted")

    # Parse the bank letter ONCE; the same evidence feeds the rules, the
    # displayed result and the saved record.
    evidence = parse_bank_letter(submission.bank_proof_text or "")

    # Stage 2 — deterministic validation
    rule = validate(submission, evidence)
    stage("validate",
          f"{len(rule.checks)} checks run — "
          f"{len(rule.errors)} blocking, {len(rule.warnings)} warnings")

    # Stage 3 — decide status
    status = decide(rule)
    stage("decision", f"Status determined: {status}")

    # Stage 4 — AI reasoning layer
    r = reason(rule)
    stage("ai_reasoning",
          "AI reasoning generated" if r["used_ai"]
          else "Deterministic reasoning (AI layer not wired)")

    # Vendor-facing actionable messages
    vendor_messages = _vendor_messages(submission, rule)
    needed = [m["message"] for m in vendor_messages] if status != "approved" else []

    run = {
        "id": run_id,
        "predecessor_stale": False,   # stale corrections are rejected, never saved
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input": _mask_input(submission.to_dict()),   # masked, letter removed
        "bank_proof_summary": _build_bank_proof_summary(submission, rule, evidence),
        "checks": [{"name": c.name, "passed": c.passed,
                    "detail": c.detail, "severity": c.severity,
                    "block": c.block} for c in rule.checks],
        "reasoning": r["narrative"],
        "ai_used": r["used_ai"],
        "vendor_messages": vendor_messages,
        "needed_back": needed,
        "stages": stages,
        "duration_ms": round((time.time() - started) * 1000, 1),
    }
    # Atomic save: verify the correction target (if any) and append in one
    # locked operation. Raises CorrectionConflict (zero attempts) for a stale,
    # closed, or unknown correction target.
    return _finalize_and_append(run, submission)


def list_runs() -> list[dict]:
    with _lock:
        return list(reversed(_runs))


def _refresh_correction_allowed(run: dict) -> dict:
    """
    Recompute whether this attempt may be corrected. Only the LATEST attempt
    of a case can be corrected, and only when it is pending. A stored flag can
    go stale when newer attempts are added, so recompute against current state.
    """
    case_id = run.get("case_id")
    if not case_id:
        run["correction_allowed"] = False
        return run
    with _lock:
        same_case = [r for r in _runs if r.get("case_id") == case_id]
    latest = max(same_case, key=lambda r: r.get("attempt_number", 1), default=None)
    run["correction_allowed"] = (
        latest is not None and run["id"] == latest["id"]
        and run.get("status") == "pending"
    )
    return run


def get_run(run_id: str) -> dict | None:
    with _lock:
        for r in _runs:
            if r["id"] == run_id:
                out = dict(r)   # copy; refresh needs the lock itself
                break
        else:
            out = None
    if out is None:
        p = OUT_DIR / f"{run_id}.json"
        if not p.exists():
            return None
        out = json.loads(p.read_text(encoding="utf-8"))
    # refresh outside the lock to avoid a non-reentrant deadlock
    return _refresh_correction_allowed(out)



def _check_phrase(name, passed):
    """Plain-language outcome phrase for a check, used in comparisons."""
    phrases = {
        "bank_account_match": ("the submitted account matched the new evidence",
                               "the submitted account differed from the bank letter"),
        "bank_proof_document": ("the bank-proof document matched the submission",
                               "the bank-proof document did not match the submission"),
        "documents_complete": ("all required documents were provided",
                               "required documents were missing"),
        "bank_holder_match": ("the bank account holder matched the registered name",
                              "the bank account holder did not match the registered name"),
        "bank_holder_present": ("the bank account holder name was provided",
                               "the bank account holder name was missing"),
        "bank_account_present": ("the bank account number was provided",
                                 "the bank account number was missing"),
        "tax_id_format": ("the tax ID was valid", "the tax ID was missing or invalid"),
        "country_supported": ("the country was supported", "the country was unsupported"),
        "vendor_name_present": ("the vendor name was provided", "the vendor name was missing"),
    }
    yes, no = phrases.get(name, ("the check passed", "the check failed"))
    return yes if passed else no


def build_comparison(current: dict, previous: dict | None) -> dict | None:
    """
    Derive a reviewer-readable comparison between a corrected attempt and its
    predecessor. Uses only recorded checks + masked evidence, so it works for
    missing documents and corrections that remain pending. Returns None for
    first attempts / older records without a predecessor (standalone view).
    """
    if not previous:
        return None

    prev_checks = {c["name"]: c for c in previous.get("checks", [])}
    cur_checks = {c["name"]: c for c in current.get("checks", [])}

    changed = []
    for name, cc in cur_checks.items():
        pc = prev_checks.get(name)
        if pc and pc.get("passed") != cc.get("passed"):
            changed.append({
                "name": name,
                "before": pc.get("passed"),
                "after": cc.get("passed"),
                "detail": cc.get("detail", ""),
            })

    # Current decision leads: what is the case's state NOW and why is it still
    # held? Then explain what improved since the previous attempt. Warnings
    # never block, so only blocking errors appear as blockers.
    cur_failed = [c for c in current.get("checks", [])
                  if not c.get("passed") and c.get("severity", "error") == "error"]
    cur_blockers = [_check_phrase(c["name"], False) for c in cur_failed]
    prev_failed = [c for c in previous.get("checks", [])
                   if not c.get("passed") and c.get("severity", "error") == "error"]
    prev_reasons = [_check_phrase(c["name"], False) for c in prev_failed]

    cur_n = current.get("attempt_number")
    prev_n = previous.get("attempt_number", 1)
    ts_txt = (f"Attempt {prev_n} at {previous.get('created_at')} \u2192 "
              f"attempt {cur_n} at {current.get('created_at')}")

    if current.get("status") == "approved":
        head = f"Attempt {cur_n} was approved"
    elif cur_blockers:
        head = f"Attempt {cur_n} is still held because {'; '.join(cur_blockers)}"
    else:
        head = f"Attempt {cur_n} decision: {current.get('status')}"

    improved = [ch for ch in changed if ch["after"]]
    regressed = [ch for ch in changed if not ch["after"]]
    if improved:
        resolved = ", ".join(_check_phrase(ch["name"], True) for ch in improved)
        resolved_txt = f"Resolved since attempt {prev_n}: {resolved}."
    elif regressed:
        resolved_txt = "No issue was resolved."
    else:
        resolved_txt = "No check outcome changed."

    summary = f"{head}. {resolved_txt} ({ts_txt})"

    return {
        "previous_run_id": previous.get("id"),
        "previous_attempt_number": prev_n,
        "previous_status": previous.get("status"),
        "previous_created_at": previous.get("created_at"),
        "previous_bank_proof_summary": previous.get("bank_proof_summary"),
        "current_attempt_number": cur_n,
        "current_status": current.get("status"),
        "current_created_at": current.get("created_at"),
        "current_bank_proof_summary": current.get("bank_proof_summary"),
        "current_blockers": cur_blockers,
        "changed_checks": changed,
        "summary": summary,
    }


def _attempt_summary(r):
    return {
        "id": r["id"],
        "attempt_number": r.get("attempt_number", 1),
        "status": r["status"],
        "created_at": r.get("created_at"),
        "duration_ms": r.get("duration_ms"),
        "reason": (r.get("reasoning", "") or "")[:120],
    }


def _group_cases(runs):
    cases = {}
    for r in runs:
        cid = r.get("case_id") or r["id"]   # legacy runs are their own case
        cases.setdefault(cid, []).append(r)
    out = []
    for cid, rs in cases.items():
        rs.sort(key=lambda x: x.get("attempt_number", 1))
        attempts = [_attempt_summary(x) for x in rs]
        vendor = rs[0].get("input", {}).get("vendor_name", "")
        out.append({
            "case_id": cid,
            "vendor": vendor,
            "latest_status": rs[-1]["status"],
            "attempt_count": len(rs),
            "attempts": attempts,
        })
    out.sort(key=lambda c: (c["attempts"][-1].get("created_at") or ""), reverse=True)
    return out


def list_cases():
    with _lock:
        return _group_cases(list(_runs))


def get_case(case_id):
    with _lock:
        runs = [r for r in _runs if (r.get("case_id") or r["id"]) == case_id]
    if not runs:
        return None
    runs.sort(key=lambda x: x.get("attempt_number", 1))
    runs = [_refresh_correction_allowed(r) for r in runs]
    return {
        "case_id": case_id,
        "vendor": runs[0].get("input", {}).get("vendor_name", ""),
        "latest_status": runs[-1]["status"],
        "attempt_count": len(runs),
        "attempts": [_attempt_summary(x) for x in runs],
        "runs": runs,
    }


# Load history on startup so the dashboard is populated after a restart.
_load_saved_runs()
