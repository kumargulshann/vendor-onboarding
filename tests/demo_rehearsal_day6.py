"""
Day 6 demo rehearsal (automated browser walkthrough — NOT a human rehearsal).

Clean-start demo history, then:
  1. One clean approval.
  2. A complete-looking submission whose account holder matches but whose
     payment account conflicts with the bank letter -> show the masked
     discrepancy and the vendor instruction.
  3. Correct it -> approval.
  4. Reopen attempts 1 and 2 to explain why the decision changed.

Times each step and prints a total so the presenter can see whether the
walkthrough fits in five minutes.
"""
import json
import time
import urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
PDF = "data/pdf/pdf_two_page_match.pdf"        # account 1234567890
ACCOUNT_FULL = "1234567890"

t0 = time.time()


def elapsed():
    return round(time.time() - t0, 1)


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(BASE)

        def wait_pill(exp):
            pg.wait_for_function(
                "t => { const e=document.querySelector('#result .pill');"
                " return !!e && e.innerText.trim().toUpperCase() === t; }",
                arg=exp, timeout=15000)

        # ---- 1. clean approval ---------------------------------------------
        t = time.time()
        pg.select_option("#example", "happy_path")
        pg.wait_for_timeout(300)
        pg.click("#runBtn")
        wait_pill("APPROVED")
        print(f"[{elapsed()}s] 1. clean approval -> APPROVED "
              f"(step {round(time.time()-t,1)}s)")

        # ---- 2. complete-looking submission with account conflict ---------
        t = time.time()
        pg.select_option("#example", "edge_account_mismatch")
        pg.wait_for_timeout(300)
        pg.click("#runBtn")
        wait_pill("PENDING")
        result = pg.inner_text("#result")
        print(f"[{elapsed()}s] 2. account-conflict submission -> PENDING "
              f"(step {round(time.time()-t,1)}s)")
        # masked discrepancy + vendor instruction present
        assert "does NOT match the account in the bank letter" in result
        assert "9999999999" not in result and ACCOUNT_FULL not in result  # masked
        held = json.loads(pg.inner_text("#log"))
        print("      masked discrepancy + vendor instruction shown")

        # ---- 3. correct it -> approval -----------------------------------
        t = time.time()
        pg.click("#correctBtn")
        pg.wait_for_timeout(250)
        pg.fill("#bank_account_number", ACCOUNT_FULL)
        pg.set_input_files("#bank_pdf", PDF)
        pg.wait_for_timeout(150)
        pg.click("#runBtn")
        wait_pill("APPROVED")
        comp = pg.inner_text("#result")
        print(f"[{elapsed()}s] 3. corrected -> APPROVED "
              f"(step {round(time.time()-t,1)}s)")
        assert "Attempt 2 was approved" in comp
        assert "the submitted account matched the new evidence" in comp

        # ---- 4. reopen attempts 1 and 2 ----------------------------------
        t = time.time()
        with urllib.request.urlopen(BASE + "/api/cases/" + held["case_id"]) as response:
            target = json.load(response)
        assert target["attempt_count"] == 2 and target["latest_status"] == "approved"
        att1 = target["attempts"][0]["id"]
        att2 = target["attempts"][1]["id"]
        # reopen attempt 1
        pg.evaluate(f"async () => {{ await showRun('{att1}'); }}")
        pg.wait_for_function(
            "id => document.getElementById('log').textContent.includes(id)",
            arg=att1, timeout=10000)
        wait_pill("PENDING")
        r1_txt = pg.inner_text("#result")
        print(f"[{elapsed()}s] 4a. reopen attempt 1 -> PENDING "
              f"(held: account mismatch) (step {round(time.time()-t,1)}s)")
        assert "does NOT match the account in the bank letter" in r1_txt
        # reopen attempt 2
        t = time.time()
        pg.evaluate(f"async () => {{ await showRun('{att2}'); }}")
        pg.wait_for_function(
            "id => document.getElementById('log').textContent.includes(id)",
            arg=att2, timeout=10000)
        wait_pill("APPROVED")
        r2_txt = pg.inner_text("#result")
        print(f"[{elapsed()}s] 4b. reopen attempt 2 -> APPROVED "
              f"(matched) (step {round(time.time()-t,1)}s)")
        assert "matched the new evidence" in r2_txt

        b.close()

    print(f"\nTotal rehearsal time: {elapsed()}s")
    print("REHEARSAL COMPLETE (automated browser walkthrough)")


if __name__ == "__main__":
    main()
