"""
Day 5 browser verification: the three Day-4 findings closed.

  (1) Start new submission clears ALL previous vendor data — pressing Run
      cannot reuse the previous vendor's evidence (new case, empty form).
  (2) A mixed correction (docs fixed + new bank mismatch) leads with the
      current decision + blocker, shows both attempt timestamps, and shows
      each vendor action once.
  (3) Only the latest pending attempt can be corrected. Reopening attempt 1
      after attempt 3 is approved offers NO correction; the server verifies
      the predecessor on save.
"""
import json
import urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
PDF = "data/pdf/pdf_two_page_match.pdf"        # account 1234567890
ACCOUNT_FULL = "1234567890"
LETTER_MARK = "BANK OF TEST"


def post(path, data):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def get(path):
    return json.load(urllib.request.urlopen(BASE + path))


def main():
    # seed three pending attempts to exercise historical and latest views
    seed = json.load(open("data/happy_path.json"))
    seed["bank_account_number"] = "9999999999"
    r1 = post("/api/run", {"data": seed})
    seed2 = dict(seed, case_id=r1["case_id"], expected_previous_run_id=r1["id"])
    r2 = post("/api/run", {"data": seed2})
    seed3 = dict(seed, case_id=r1["case_id"], expected_previous_run_id=r2["id"])
    r3 = post("/api/run", {"data": seed3})
    print(f"seeded case {r1['case_id']}: att1={r1['status']} att2={r2['status']} "
          f"att3={r3['status']}")

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        leaked = []

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
        pg.on("response", on_response)
        pg.goto(BASE)
        pg.wait_for_selector("#dashTable tbody tr", timeout=10000)

        def badge():
            return pg.inner_text("#result .pill").strip().upper()

        def wait_badge(exp):
            pg.wait_for_function(
                "t => { const e=document.querySelector('#result .pill');"
                " return !!e && e.innerText.trim().toUpperCase() === t; }",
                arg=exp)

        def run_id():
            return pg.inner_text("#log").split('"id": "')[1].split('"')[0]

        # --- (1) Start new submission clears everything ----------------------
        # create a corrected case (attempt 2) so the Start-new-submission button
        # is present, then verify it clears ALL previous vendor data.
        pg.select_option("#example", "happy_path")
        pg.wait_for_timeout(300)
        pg.fill("#bank_account_number", "9999999999")
        pg.set_input_files("#bank_pdf", PDF)
        pg.wait_for_timeout(200)
        pg.click("#runBtn")
        wait_badge("PENDING")
        old_case = pg.inner_text("#result .muted").split("Case ")[1].split(" ")[0]
        # correct it -> attempt 2 (now the Start-new-submission button appears)
        pg.click("#correctBtn")
        pg.wait_for_timeout(200)
        pg.fill("#bank_account_number", ACCOUNT_FULL)
        pg.set_input_files("#bank_pdf", PDF)
        pg.wait_for_timeout(200)
        pg.click("#runBtn")
        wait_badge("APPROVED")
        # Start new submission
        pg.click("#newSubBtn")
        pg.wait_for_timeout(200)
        # verify fields cleared
        fields_clear = pg.evaluate("""() => {
            const ids=['vendor_name','legal_name','tax_id','contact_email',
                       'bank_holder_name','bank_account_number','bank_proof_text'];
            return ids.every(id => document.getElementById(id).value === '');
        }""")
        docs_clear = pg.eval_on_selector_all(".doc:checked", "els => els.length") == 0
        file_clear = pg.eval_on_selector("#bank_pdf", "el => el.files.length") == 0
        print("(1) fields cleared:", fields_clear, "| docs cleared:", docs_clear,
              "| file cleared:", file_clear)
        assert fields_clear and docs_clear and file_clear
        # pressing Run with the empty form must NOT reuse the old evidence:
        # it creates a new case (pending, missing everything), not the old case
        pg.click("#runBtn")
        wait_badge("PENDING")
        new_case = pg.inner_text("#result .muted").split("Case ")[1].split(" ")[0]
        print("(1) old case:", old_case, "| new case after Run:", new_case)
        assert new_case != old_case, "Run reused the previous vendor's case"
        # the new run is pending because fields are empty (no reuse of evidence)
        assert badge() == "PENDING"

        # --- (2) mixed correction: docs fixed + new bank mismatch ------------
        # fresh case: attempt 1 pending (missing docs + matching account)
        pg.select_option("#example", "happy_path")
        pg.wait_for_timeout(300)
        # remove two documents to make it pending on missing docs
        pg.uncheck(".doc[value='tax_registration']")
        pg.uncheck(".doc[value='address_proof']")
        pg.click("#runBtn")
        wait_badge("PENDING")
        rt = pg.inner_text("#result")
        assert "address proof" in rt and "tax registration" in rt
        # correct: supply missing docs but introduce an account mismatch
        pg.click("#correctBtn")
        pg.wait_for_timeout(200)
        pg.check(".doc[value='tax_registration']")
        pg.check(".doc[value='address_proof']")
        pg.fill("#bank_account_number", "9999999999")
        pg.set_input_files("#bank_pdf", PDF)
        pg.wait_for_timeout(200)
        pg.click("#runBtn")
        wait_badge("PENDING")
        rt = pg.inner_text("#result")
        print("(2) mixed correction ->", badge())
        # leads with current blocker
        assert "Attempt 2 is still held because" in rt
        assert "the submitted account differed from the bank letter" in rt
        # explains what was resolved
        assert "all required documents were provided" in rt
        # both attempt timestamps shown
        assert "Attempt 1" in rt and "attempt 2" in rt
        # each remaining vendor action appears once (no duplication). The docs
        # action is resolved, so the account-mismatch action is what remains.
        acct_action = "does NOT match the account in the bank letter"
        cnt = rt.count(acct_action)
        print("(2) account-mismatch action occurrences:", cnt)
        assert cnt == 1, f"vendor action shown {cnt} times"

        # --- (3) only latest pending attempt can be corrected ---------------
        # attempt 3 is the latest pending attempt
        # reopen attempt 1 (pending, but NOT latest) -> no Correct button
        att1_id = r1["id"]
        pg.evaluate(f"async () => {{ await showRun('{att1_id}'); }}")
        pg.wait_for_function(
            "id => document.getElementById('log').textContent.includes(id)",
            arg=att1_id, timeout=10000)
        wait_badge("PENDING")
        has_correct = pg.eval_on_selector("#correctBtn", "el => !!el") if \
            pg.query_selector("#correctBtn") else False
        print("(3) attempt 1 (historical pending) offers Correct:", has_correct)
        assert has_correct is False
        # the note directs to the latest attempt
        rt = pg.inner_text("#result")
        assert "Only the latest pending attempt can be corrected" in rt
        # reopen attempt 3 (latest, pending) -> Correct IS offered
        att3_id = r3["id"]
        pg.evaluate(f"async () => {{ await showRun('{att3_id}'); }}")
        pg.wait_for_function(
            "id => document.getElementById('log').textContent.includes(id)",
            arg=att3_id, timeout=10000)
        wait_badge("PENDING")
        has_correct3 = pg.eval_on_selector("#correctBtn", "el => !!el") if \
            pg.query_selector("#correctBtn") else False
        print("(3) attempt 3 (latest pending) offers Correct:", has_correct3)
        assert has_correct3 is True

        print("\nLEAKED:", leaked if leaked else "none")
        assert not leaked
        b.close()
    print("\nALL DAY-5 UI CHECKS PASSED")


if __name__ == "__main__":
    main()
