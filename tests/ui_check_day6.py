"""
Day 6 browser acceptance: a stale correction cannot change case history.

Scenario (run twice — once where tab A's correction stays pending, once where
it approves):
  1. Open the same pending attempt (attempt 1) in two tabs.
  2. Tab A saves a correction -> the server appends attempt 2.
  3. Tab B submits its still-open form (which still targets attempt 1).
  4. The case must still contain EXACTLY two attempts, the latest decision
     must be unchanged, and tab B must show a readable conflict message with a
     way to open the latest attempt (attempt 2).

This is an automated browser check (Playwright), NOT a human rehearsal.
"""
import json
import urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
PDF = "data/pdf/pdf_two_page_match.pdf"        # account 1234567890
ACCOUNT_FULL = "1234567890"
WRONG = "9999999999"
LETTER_MARK = "BANK OF TEST"


def post(path, data):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def get(path):
    return json.load(urllib.request.urlopen(BASE + path))


def seed_pending_case():
    """A case with one pending attempt (account mismatch)."""
    seed = json.load(open("data/happy_path.json"))
    seed["bank_account_number"] = WRONG
    r1 = post("/api/run", {"data": seed})
    return r1


def open_attempt_and_correct(pg, att_id):
    """Open an attempt in a tab and click Correct (loads the form)."""
    pg.evaluate(f"async () => {{ await showRun('{att_id}'); }}")
    pg.wait_for_function(
        "id => document.getElementById('log').textContent.includes(id)",
        arg=att_id, timeout=10000)
    # wait for the status pill
    pg.wait_for_function(
        "() => { const e=document.querySelector('#result .pill');"
        " return !!e && e.innerText.trim().toUpperCase() === 'PENDING'; }")
    assert pg.query_selector("#correctBtn"), "Correct button missing on pending attempt"
    pg.click("#correctBtn")
    pg.wait_for_timeout(250)


def wait_pill(pg, exp):
    pg.wait_for_function(
        "t => { const e=document.querySelector('#result .pill');"
        " return !!e && e.innerText.trim().toUpperCase() === t; }",
        arg=exp, timeout=15000)


def run_form(pg):
    pg.click("#runBtn")


def one_scenario(pg_a, pg_b, case_label, tab_a_account, expected_latest):
    """Run the two-tab scenario. Returns (attempt_count, latest_status)."""
    print(f"\n=== {case_label} ===")
    r1 = seed_pending_case()
    case_id = r1["case_id"]
    att1 = r1["id"]
    print(f"seeded {case_id}: attempt 1 pending ({att1})")

    # both tabs open attempt 1 and click Correct -> both hold a form whose
    # expected_previous_run_id is attempt 1
    open_attempt_and_correct(pg_a, att1)
    open_attempt_and_correct(pg_b, att1)
    print("both tabs opened attempt 1 and clicked Correct")

    # Tab A saves its correction -> attempt 2
    if tab_a_account == ACCOUNT_FULL:
        pg_a.fill("#bank_account_number", ACCOUNT_FULL)
        pg_a.set_input_files("#bank_pdf", PDF)
    else:
        pg_a.fill("#bank_account_number", WRONG)   # still a mismatch -> pending
        pg_a.set_input_files("#bank_pdf", PDF)
    pg_a.wait_for_timeout(150)
    run_form(pg_a)
    wait_pill(pg_a, "APPROVED" if tab_a_account == ACCOUNT_FULL else "PENDING")
    att2 = pg_a.inner_text("#log").split('"id": "')[1].split('"')[0]
    print(f"tab A saved -> attempt 2 ({att2})")

    # Tab B submits its still-open form (targets attempt 1 -> stale)
    pg_b.fill("#bank_account_number", ACCOUNT_FULL)
    pg_b.set_input_files("#bank_pdf", PDF)
    run_form(pg_b)
    # tab B must NOT get a result; it must show a conflict
    pg_b.wait_for_function(
        "() => { const s=document.getElementById('status');"
        " return s && s.innerText.toLowerCase().includes('latest attempt'); }",
        timeout=15000)
    status_text = pg_b.inner_text("#status")
    print("tab B conflict:", status_text[:90].replace("\n", " "))
    assert "Open latest attempt" in status_text or "latest attempt" in status_text.lower()
    # tab B must NOT show a new result pill (no attempt 3)
    has_pill_b = pg_b.query_selector("#result .pill")
    assert has_pill_b is None, "tab B created a new attempt (stale save not blocked)"
    assert pg_b.is_disabled("#runBtn"), "stale draft remains submittable"

    # verify via the API: exactly 2 attempts, latest decision unchanged
    case = get(f"/api/cases/{case_id}")
    attempts = case["attempts"]
    print(f"case {case_id}: attempt_count={case['attempt_count']} "
          f"latest={case['latest_status']}")
    assert case["attempt_count"] == 2, f"expected 2 attempts, got {case['attempt_count']}"
    assert [a["attempt_number"] for a in attempts] == [1, 2]
    assert case["latest_status"] == expected_latest
    # the latest attempt id is tab A's attempt 2
    assert attempts[-1]["id"] == att2

    # tab B offers a way to open the latest attempt -> loads attempt 2
    assert pg_b.query_selector("#openLatestBtn"), "no Open-latest button"
    pg_b.click("#openLatestBtn")
    pg_b.wait_for_function(
        "id => document.getElementById('log').textContent.includes(id)",
        arg=att2, timeout=10000)
    wait_pill(pg_b, "APPROVED" if expected_latest == "approved" else "PENDING")
    print("tab B opened latest attempt 2 via the conflict button")

    # clean up this case's runs from the dashboard view is not needed; the next
    # scenario seeds a fresh case
    return case["attempt_count"], case["latest_status"]


def main():
    leaked = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg_a = b.new_page()
        pg_b = b.new_page()

        def on_response(resp):
            u = resp.url
            if "/api/examples" in u:
                return
            ct = resp.headers.get("content-type", "")
            if "json" in ct or "text/event-stream" in ct:
                try:
                    body = resp.text()
                    if LETTER_MARK in body or ACCOUNT_FULL in body:
                        leaked.append(u)
                except Exception:
                    pass
        pg_a.on("response", on_response)
        pg_b.on("response", on_response)

        pg_a.goto(BASE)
        pg_b.goto(BASE)

        # Scenario 1: tab A's correction stays pending (still a mismatch)
        n1, latest1 = one_scenario(pg_a, pg_b, "Scenario 1: tab A -> pending",
                                    WRONG, "pending")
        # Scenario 2: tab A's correction approves (matching account)
        n2, latest2 = one_scenario(pg_a, pg_b, "Scenario 2: tab A -> approved",
                                    ACCOUNT_FULL, "approved")

        print("\nLEAKED:", leaked if leaked else "none")
        assert not leaked
        b.close()

    print(f"\nTwo-tab attempt counts: scenario1={n1} attempts (latest={latest1}), "
          f"scenario2={n2} attempts (latest={latest2})")
    print("ALL DAY-6 TWO-TAB UI CHECKS PASSED")


if __name__ == "__main__":
    main()
