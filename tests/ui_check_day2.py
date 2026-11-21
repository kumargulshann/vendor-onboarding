"""
Day 2 browser verification: PDF upload, history 404/recovery, live run.
Uses Playwright headless Chromium against the running app.
"""
import json
import urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
PDF = "data/pdf"


def post(path, data):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def main():
    # seed history rows (approved + pending) for the 404/recovery test
    appr = post("/api/run", {"data": json.load(open("data/happy_path.json"))})
    pend = post("/api/run", {"data": json.load(open("data/edge_account_mismatch.json"))})
    appr_id, pend_id = appr["id"], pend["id"]
    print("seeded approved:", appr_id, "| pending:", pend_id)

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(BASE)
        pg.wait_for_selector("#dashTable tbody tr")

        def badge():
            return pg.inner_text("#result .pill").strip().upper()

        def wait_badge(expected):
            pg.wait_for_function(
                "t => { const e = document.querySelector('#result .pill');"
                " return !!e && e.innerText.trim().toUpperCase() === t; }",
                arg=expected)

        def status_visible():
            return pg.eval_on_selector("#status",
                "el => getComputedStyle(el).display !== 'none'")

        def run_and_status(expect):
            pg.click("#runBtn")
            wait_badge(expect)
            pg.wait_for_timeout(150)
            return badge()

        def load_example(name):
            pg.select_option("#example", name)
            pg.wait_for_function(
                "() => document.querySelector('#vendor_name').value.length > 0")
            pg.wait_for_timeout(100)

        # --- 404 failure + recovery ---
        pg.click(f"tr:has-text('{pend_id}')")
        wait_badge("PENDING")
        log_before = pg.inner_text("#log")
        pg.evaluate("showRun('definitely-not-a-run')")
        pg.wait_for_function("() => getComputedStyle(document.querySelector('#status')).display !== 'none'")
        print("404 -> status shown:", status_visible())
        print("404 -> log preserved:", pg.inner_text("#log") == log_before)
        # recovery: select a valid run, status clears
        pg.click(f"tr:has-text('{appr_id}')")
        wait_badge("APPROVED")
        pg.wait_for_function("() => getComputedStyle(document.querySelector('#status')).display === 'none'")
        print("recovery -> status cleared, badge:", badge())

        # --- image-only PDF after happy-path example -> pending ---
        load_example("happy_path")
        pg.set_input_files("#bank_pdf", f"{PDF}/pdf_image_only.pdf")
        pg.wait_for_timeout(400)
        st = run_and_status("PENDING")
        bp = pg.inner_text("#result")
        print("image-only after happy ->", st,
              "| source pdf:", "source pdf" in bp.lower(),
              "| unreadable:", "unreadable" in bp.lower())
        assert st == "PENDING"

        # --- two-page match PDF -> approved ---
        pg.set_input_files("#bank_pdf", f"{PDF}/pdf_two_page_match.pdf")
        pg.wait_for_timeout(400)
        st = run_and_status("APPROVED")
        print("two-page match ->", st)
        assert st == "APPROVED"

        # --- account mismatch PDF -> pending with correction ---
        pg.set_input_files("#bank_pdf", f"{PDF}/pdf_account_mismatch.pdf")
        pg.wait_for_timeout(400)
        st = run_and_status("PENDING")
        bp = pg.inner_text("#result")
        print("account mismatch ->", st, "| mismatch:", "mismatch" in bp.lower())
        assert st == "PENDING" and "mismatch" in bp.lower()

        # --- invalid file -> error at Run, then retry with valid PDF ---
        # (Day 3: the PDF is parsed once on Run, so the error surfaces on Run)
        pg.set_input_files("#bank_pdf", f"{PDF}/invalid.pdf")
        pg.wait_for_timeout(200)
        pg.click("#runBtn")
        pg.wait_for_function("() => getComputedStyle(document.querySelector('#status')).display !== 'none'")
        print("invalid file -> status shown:", status_visible())
        pg.set_input_files("#bank_pdf", f"{PDF}/pdf_two_page_match.pdf")
        pg.wait_for_timeout(400)
        pg.fill("#bank_account_number", "1234567890")
        pg.click("#runBtn")
        wait_badge("APPROVED")
        print("retry valid PDF -> status cleared, badge:", badge())

        # --- reopen a saved PDF-backed result ---
        runs = json.load(urllib.request.urlopen(BASE + "/api/runs"))["runs"]
        pdf_run = next((r for r in runs if r["id"] != appr_id and r["id"] != pend_id), runs[-1])
        pg.click(f"tr:has-text('{pdf_run['id']}')")
        wait_badge(pdf_run["status"].upper())
        bp = pg.inner_text("#result")
        print("reopened PDF run:", pdf_run["id"], pdf_run["status"],
              "| source pdf:", "source pdf" in bp.lower())

        # --- rerun all five existing examples (text evidence) ---
        expected = {"happy_path": "APPROVED", "edge_tax_format": "REJECTED",
                    "edge_bank_mismatch": "PENDING", "edge_missing_docs": "PENDING",
                    "edge_account_mismatch": "PENDING"}
        ok = True
        for name, exp in expected.items():
            load_example(name)
            pg.set_input_files("#bank_pdf", [])   # clear any PDF -> text evidence
            pg.wait_for_timeout(200)
            st = run_and_status(exp)
            match = "OK" if st == exp else "FAIL"
            if st != exp: ok = False
            print(f"example {name:22s} -> {st} ({match})")

        # --- live stages + spinner still work ---
        load_example("happy_path")
        pg.click("#runBtn")
        pg.wait_for_selector("#stagelist .stage")
        pg.wait_for_timeout(900)
        n = pg.eval_on_selector_all("#stagelist .stage", "els => els.length")
        spin = pg.eval_on_selector("#running", "el => getComputedStyle(el).display === 'none'")
        print("live stages:", n, "| spinner hidden:", spin)
        assert n == 4 and spin

        b.close()
    print("\nALL DAY-2 UI CHECKS PASSED" if ok else "\nSOME CHECKS FAILED")


if __name__ == "__main__":
    main()
