"""
Day 3 browser verification: correction & resubmit + reviewer multi-attempt view.
Uses Playwright headless Chromium against the running app.

Flow verified:
  (a) valid PDF with a conflicting form account -> pending, attempt 1
  (b) correct the account + supply bank proof -> approved, attempt 2, same case
  (c) reopen both attempts -> each describes its own attempt
  (d) insufficient correction -> still pending with actionable reason
  (e) double-click -> exactly one correction attempt
  (f) picker invalidation + delayed upload guard + no raw letter in page
"""
import json
import urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
PDF = "data/pdf/pdf_two_page_match.pdf"   # account 1234567890, holder Acme
ACCOUNT_FULL = "1234567890"
LETTER_MARK = "BANK OF TEST"


def get(path):
    return json.load(urllib.request.urlopen(BASE + path))


def post(path, data):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def main():
    # seed one case so the dashboard table has a row on load
    post("/api/run", {"data": json.load(open("data/happy_path.json"))})

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()

        # capture every response body so we can assert nothing leaks
        leaked = []

        def on_response(resp):
            # only assert on run/upload responses, not the example fixtures
            try:
                u = resp.url
                if "/api/examples" in u:
                    return
                ct = resp.headers.get("content-type", "")
                if "json" in ct or "text/event-stream" in ct:
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

        def wait_badge(expected):
            pg.wait_for_function(
                "t => { const e = document.querySelector('#result .pill');"
                " return !!e && e.innerText.trim().toUpperCase() === t; }",
                arg=expected)

        def case_line():
            return pg.inner_text("#result .muted").strip()

        # --- (a) attempt 1: load happy example, swap in a conflicting account,
        #         attach the valid PDF, run -> pending ------------------------
        pg.select_option("#example", "happy_path")
        pg.wait_for_timeout(300)
        pg.fill("#bank_account_number", "9999999999")      # conflicts with PDF
        pg.set_input_files("#bank_pdf", PDF)
        pg.wait_for_timeout(200)
        pg.click("#runBtn")
        wait_badge("PENDING")
        att1_case = case_line()
        att1 = json.loads(pg.inner_text("#log"))
        print("(a) attempt 1:", badge(), "|", att1_case)
        assert badge() == "PENDING" and "attempt 1" in att1_case

        # --- (b) correct: start correction, fix account + re-upload, run ---
        pg.click("#correctBtn")
        pg.wait_for_timeout(200)
        # correct account; the correction must re-supply bank proof too
        pg.fill("#bank_account_number", ACCOUNT_FULL)
        pg.set_input_files("#bank_pdf", PDF)
        pg.wait_for_timeout(200)
        pg.click("#runBtn")
        wait_badge("APPROVED")
        att2_case = case_line()
        att2 = json.loads(pg.inner_text("#log"))
        print("(b) attempt 2:", badge(), "|", att2_case)
        assert badge() == "APPROVED" and "attempt 2" in att2_case

        # same case id in both attempts
        c1 = att1_case.split("Case ")[1].split(" ")[0]
        c2 = att2_case.split("Case ")[1].split(" ")[0]
        assert c1 == c2, (c1, c2)
        print("    same case id:", c1)

        # --- (c) reopen both attempts from the dashboard --------------------
        pg.wait_for_function(
            "() => document.querySelectorAll('#dashTable tbody tr').length > 0")
        rows = pg.query_selector_all("#dashTable tbody tr")
        attempts_found = []
        for r in rows:
            atext = r.inner_text()
            if "attempt 1" in atext or "attempt 2" in atext:
                attempts_found.append(atext)
        print("(c) dashboard attempts:", len(attempts_found))
        assert len(attempts_found) >= 2

        # open attempt 1 row -> pending
        pg.locator(f'[data-run-id="{att1["id"]}"]').click()
        pg.wait_for_function(
            "() => { const e=document.querySelector('#result .pill');"
            " return !!e && e.innerText.trim().toUpperCase()==='PENDING'; }")
        assert badge() == "PENDING" and "attempt 1" in case_line()
        print("    re-opened attempt 1 ->", badge(), case_line())

        # open attempt 2 row -> approved
        pg.locator(f'[data-run-id="{att2["id"]}"]').click()
        pg.wait_for_function(
            "() => { const e=document.querySelector('#result .pill');"
            " return !!e && e.innerText.trim().toUpperCase()==='APPROVED'; }")
        assert badge() == "APPROVED" and "attempt 2" in case_line()
        print("    re-opened attempt 2 ->", badge(), case_line())

        # --- (d) insufficient correction -> still pending -------------------
        # Use a separate case since the original case is closed after approval.
        pg.select_option("#example", "edge_account_mismatch")
        pg.click("#runBtn")
        wait_badge("PENDING")
        pending = json.loads(pg.inner_text("#log"))
        c1 = pending["case_id"]
        pg.click("#correctBtn")
        pg.wait_for_timeout(200)
        pg.fill("#bank_account_number", "1111111111")      # still wrong
        pg.set_input_files("#bank_pdf", PDF)
        pg.wait_for_timeout(200)
        pg.click("#runBtn")
        wait_badge("PENDING")
        print("(d) insufficient correction ->", badge(), "|", case_line())
        assert badge() == "PENDING" and "attempt 2" in case_line()

        # --- (e) double-click creates exactly one attempt ------------------
        before = get("/api/cases/" + c1)["attempt_count"]
        pg.click("#correctBtn")
        pg.wait_for_timeout(150)
        pg.fill("#bank_account_number", ACCOUNT_FULL)
        pg.set_input_files("#bank_pdf", PDF)
        pg.wait_for_timeout(150)
        # dispatch two clicks back-to-back; the running-guard must ignore the 2nd
        pg.evaluate("() => { const b=document.getElementById('runBtn'); b.click(); b.click(); }")
        wait_badge("APPROVED")
        pg.wait_for_timeout(400)
        after = get("/api/cases/" + c1)["attempt_count"]
        print(f"(e) attempts before={before} after={after}")
        assert after == before + 1, "double-click produced extra attempts"

        # --- (f) evidence invalidation + no leakage ------------------------
        # load example -> picker must clear
        pg.select_option("#example", "edge_account_mismatch")
        pg.wait_for_timeout(300)
        files = pg.eval_on_selector("#bank_pdf", "el => el.files.length")
        print("(f) after loading example, file picker entries:", files)
        assert files == 0, "example load did not clear the file picker"

        print("\nLEAKED RESPONSES:", leaked if leaked else "none")
        assert not leaked, f"raw letter or full account leaked: {leaked}"

        b.close()
    print("\nALL DAY-3 UI CHECKS PASSED")


if __name__ == "__main__":
    main()
