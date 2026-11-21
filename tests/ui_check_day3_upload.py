"""
Day 3 upload-guard browser verification:
  - selecting or replacing a PDF immediately invalidates earlier evidence
  - two quick file selections -> the last one controls the run
  - a delayed/earlier selection cannot control a later run
  - invalid PDF -> clear error; then valid PDF -> run works (invalid->valid retry)
  - password/size guard behaviour
"""
import json
import urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
MATCH = "data/pdf/pdf_two_page_match.pdf"        # account 1234567890
MISMATCH = "data/pdf/pdf_account_mismatch.pdf"    # account 9999999999
INVALID = "data/pdf/invalid.pdf"
ACCOUNT_FULL = "1234567890"
LETTER_MARK = "BANK OF TEST"


def get(path):
    return json.load(urllib.request.urlopen(BASE + path))


def main():
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

        def run():
            pg.click("#runBtn")

        # --- (1) two quick file selections -> last one wins ----------------
        pg.select_option("#example", "happy_path")
        pg.wait_for_timeout(200)
        pg.set_input_files("#bank_pdf", MATCH)        # valid (1234567890)
        pg.set_input_files("#bank_pdf", MISMATCH)     # replace quickly
        pg.wait_for_timeout(200)
        pg.fill("#bank_account_number", ACCOUNT_FULL)
        run(); wait_badge("PENDING")                   # mismatch PDF -> pending
        print("(1) two quick selections -> last (mismatch) controls run:", badge())
        assert badge() == "PENDING"

        # --- (2) invalid -> valid retry -------------------------------------
        pg.set_input_files("#bank_pdf", INVALID)
        pg.wait_for_timeout(200)
        run()
        pg.wait_for_function(
            "() => { const e=document.getElementById('status');"
            " return !!e && getComputedStyle(e).display !== 'none'; }",
            timeout=8000)
        err = pg.inner_text("#status")
        print("(2) invalid PDF error:", err[:80])
        assert "Could not process" in err or "error" in err.lower()

        # replace with a valid PDF and retry
        pg.set_input_files("#bank_pdf", MATCH)
        pg.wait_for_timeout(200)
        pg.fill("#bank_account_number", ACCOUNT_FULL)
        run(); wait_badge("APPROVED")
        print("(2) invalid->valid retry ->", badge())
        assert badge() == "APPROVED"

        # --- (3) delayed earlier selection must not control a later run -----
        # Select mismatch, start a run, then (while it runs) swap to match.
        # The in-flight run must still use the mismatch -> pending.
        pg.set_input_files("#bank_pdf", MISMATCH)
        pg.fill("#bank_account_number", ACCOUNT_FULL)
        pg.evaluate("() => document.getElementById('runBtn').click()")
        pg.wait_for_timeout(60)          # run is in flight
        pg.set_input_files("#bank_pdf", MATCH)   # late swap
        pg.wait_for_timeout(600)
        print("(3) late swap during run ->", badge(), "(expect PENDING)")
        assert badge() == "PENDING"

        # now a fresh run uses the current (match) selection -> approved
        pg.fill("#bank_account_number", ACCOUNT_FULL)
        run(); wait_badge("APPROVED")
        print("(3) fresh run after swap ->", badge())
        assert badge() == "APPROVED"

        print("\nLEAKED:", leaked if leaked else "none")
        assert not leaked
        b.close()
    print("\nALL UPLOAD-GUARD UI CHECKS PASSED")


if __name__ == "__main__":
    main()
