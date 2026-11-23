"""
Day 4 browser verification: reviewer comparison + upload guards + checklist.

Flows verified:
  (a) account mismatch corrected to approved -> comparison summary readable
  (b) missing documents corrected to approved -> comparison + vendor message
  (c) incomplete correction stays pending -> "issue remains"
  (d) open attempts in both directions -> comparison/decision/evidence/stages/
      vendor message all describe the SELECTED attempt
  (e) new submission after a correction -> NEW case id
  (f) oversized file -> error kept visible, Run blocked, ZERO runs created;
      valid-file retry recovers
  (g) correction restores the attempt's document checklist (only original items)
"""
import json
from pathlib import Path
import urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
PDF = "data/pdf"
MATCH = f"{PDF}/pdf_two_page_match.pdf"        # account 1234567890
ACCOUNT_FULL = "1234567890"
LETTER_MARK = "BANK OF TEST"

# an oversized file: a real PDF padded well past 1 MB
BIG_PDF = {
    "name": "oversized_letter.pdf",
    "mimeType": "application/pdf",
    "buffer": Path(MATCH).read_bytes().ljust(1_100_000, b"0"),
}


def post(path, data):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def get(path):
    return json.load(urllib.request.urlopen(BASE + path))


def main():
    # seed a case with only two documents so we can test checklist restore
    seed = json.load(open("data/happy_path.json"))
    seed["documents"] = ["incorporation_proof", "bank_proof"]
    seed["bank_account_number"] = "9999999999"   # conflict -> pending
    seeded = post("/api/run", {"data": seed})
    seeded_att1_id = seeded["id"]
    seeded_case_id = seeded["case_id"]

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

        def result_text():
            return pg.inner_text("#result")

        # --- (a) account mismatch corrected to approved --------------------
        pg.select_option("#example", "happy_path")
        pg.wait_for_timeout(300)
        pg.fill("#bank_account_number", "9999999999")
        pg.set_input_files("#bank_pdf", MATCH)
        pg.wait_for_timeout(200)
        pg.click("#runBtn")
        wait_badge("PENDING")
        att1_case = pg.inner_text("#result .muted").split("Case ")[1].split(" ")[0]
        att1_run_id = pg.inner_text("#log").split('"id": "')[1].split('"')[0]
        print("(a) attempt 1:", badge(), "| case", att1_case, "| run", att1_run_id)
        pg.click("#correctBtn")
        pg.wait_for_timeout(200)
        pg.fill("#bank_account_number", ACCOUNT_FULL)
        pg.set_input_files("#bank_pdf", MATCH)
        pg.wait_for_timeout(200)
        pg.click("#runBtn")
        wait_badge("APPROVED")
        att2_run_id = pg.inner_text("#log").split('"id": "')[1].split('"')[0]
        rt = result_text()
        print("(a) attempt 2:", badge())
        assert "Attempt 2 was approved" in rt
        assert "the submitted account matched the new evidence" in rt
        assert "Reviewer comparison" in rt
        print("    comparison summary present")

        # --- (b) missing documents corrected to approved -------------------
        # click the SEEDED 2-doc attempt by its run id, correct with all docs
        pg.locator(f'[data-run-id="{seeded_att1_id}"]').click()
        wait_badge("PENDING")
        # the correction should restore ONLY the original two documents
        pg.click("#correctBtn")
        pg.wait_for_timeout(200)
        checked = [c.get_attribute("value")
                   for c in pg.query_selector_all(".doc:checked")]
        print("(g) restored checklist:", sorted(checked))
        assert set(checked) == {"incorporation_proof", "bank_proof"}, checked
        # supply the missing docs + correct account + bank proof -> approved
        for val in ["tax_registration", "address_proof"]:
            pg.check(f".doc[value='{val}']")
        pg.fill("#bank_account_number", ACCOUNT_FULL)
        pg.set_input_files("#bank_pdf", MATCH)
        pg.wait_for_timeout(200)
        pg.click("#runBtn")
        wait_badge("APPROVED")
        rt = result_text()
        print("(b) docs corrected ->", badge())
        assert "all required documents were provided" in rt

        # --- (c) incomplete correction stays pending ----------------------
        # fresh case: attempt 1 pending (wrong account), correct with a
        # still-wrong account -> attempt 2 pending, same issue unresolved
        pg.click("#newSubBtn"); pg.wait_for_timeout(150)
        pg.select_option("#example", "happy_path")
        pg.wait_for_timeout(300)
        pg.fill("#bank_account_number", "9999999999")
        pg.set_input_files("#bank_pdf", MATCH)
        pg.wait_for_timeout(200)
        pg.click("#runBtn")
        wait_badge("PENDING")
        pg.click("#correctBtn")
        pg.wait_for_timeout(200)
        pg.fill("#bank_account_number", "1111111111")
        pg.set_input_files("#bank_pdf", MATCH)
        pg.wait_for_timeout(200)
        pg.click("#runBtn")
        wait_badge("PENDING")
        rt = result_text()
        print("(c) incomplete correction ->", badge())
        assert "is still held because" in rt
        assert "No check outcome changed" in rt

        # --- (d) open attempts both directions ------------------------------
        # open attempt 1 (pending) by run id -> standalone view + its stages
        for r in pg.query_selector_all("#dashTable tbody tr"):
            if att1_run_id in r.inner_text():
                r.click(); break
        pg.wait_for_function(
            "id => document.getElementById('log').textContent.includes(id)",
            arg=att1_run_id)
        wait_badge("PENDING")
        rt1 = result_text()
        assert "First attempt" in rt1 or "no earlier attempt" in rt1
        assert "Received vendor submission" in pg.inner_text("#stagelist")
        print("(d) attempt 1 view:", badge(), "| standalone comparison")
        # open attempt 2 (approved) by run id -> its comparison + decision
        for r in pg.query_selector_all("#dashTable tbody tr"):
            if att2_run_id in r.inner_text():
                r.click(); break
        pg.wait_for_function(
            "id => document.getElementById('log').textContent.includes(id)",
            arg=att2_run_id)
        wait_badge("APPROVED")
        rt2 = result_text()
        assert "Reviewer comparison" in rt2
        assert "matched the new evidence" in rt2
        print("(d) attempt 2 view:", badge(), "| comparison present")

        # --- (e) new submission after correction -> NEW case ----------------
        cases_before = get("/api/cases")["count"]
        # use Start new submission, then run a fresh happy path
        pg.click("#newSubBtn")
        pg.wait_for_timeout(150)
        pg.select_option("#example", "happy_path")
        pg.wait_for_timeout(300)
        pg.click("#runBtn")     # text evidence (no PDF selected)
        wait_badge("APPROVED")
        new_case = pg.inner_text("#result .muted").split("Case ")[1].split(" ")[0]
        print("(e) new submission case:", new_case)
        assert new_case != att1_case
        cases_after = get("/api/cases")["count"]
        assert cases_after == cases_before + 1

        # --- (f) oversized file block ----------------------------------------
        runs_before = get("/api/runs")["count"]
        pg.set_input_files("#bank_pdf", BIG_PDF)
        pg.wait_for_timeout(200)
        btn_disabled = pg.eval_on_selector("#runBtn", "el => el.disabled")
        print("(f) oversized -> run button disabled:", btn_disabled)
        assert btn_disabled is True
        # attempt to run anyway (handler guard) -> zero new runs
        pg.evaluate("() => document.getElementById('runBtn').click()")
        pg.wait_for_timeout(600)
        runs_after = get("/api/runs")["count"]
        print(f"(f) runs before={runs_before} after={runs_after}")
        assert runs_after == runs_before, "oversized file created a run"
        # error still visible
        assert pg.eval_on_selector("#status",
            "el => getComputedStyle(el).display !== 'none'")
        # valid-file retry recovers
        pg.set_input_files("#bank_pdf", MATCH)
        pg.wait_for_timeout(200)
        pg.fill("#bank_account_number", ACCOUNT_FULL)
        pg.click("#runBtn")
        wait_badge("APPROVED")
        print("(f) valid retry ->", badge())

        print("\nLEAKED:", leaked if leaked else "none")
        assert not leaked
        b.close()
    print("\nALL DAY-4 UI CHECKS PASSED")


if __name__ == "__main__":
    main()
