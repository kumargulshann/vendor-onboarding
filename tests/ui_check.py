"""
Browser verification for Issue 2 (dashboard consistency) and live-run behaviour.
Uses Playwright headless Chromium against the running app.
"""
import json
import urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"


def post(path, data):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def main():
    # seed one approved and one pending run
    hp = json.load(open("data/happy_path.json"))
    em = json.load(open("data/edge_account_mismatch.json"))
    appr = post("/api/run", {"data": hp})
    pend = post("/api/run", {"data": em})
    appr_id, pend_id = appr["id"], pend["id"]
    print("seeded approved:", appr_id, "| pending:", pend_id)

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(BASE)
        pg.wait_for_selector("#dashTable tbody tr")

        def badge():
            return pg.inner_text("#result .pill").strip().upper()

        def stage_text():
            return pg.inner_text("#stagelist")

        def wait_badge(expected):
            pg.wait_for_function(
                "t => { const e = document.querySelector('#result .pill');"
                " return !!e && e.innerText.trim().toUpperCase() === t; }",
                arg=expected)

        def wait_stage(expected):
            pg.wait_for_function(
                "t => { const e = document.querySelector('#stagelist');"
                " return !!e && e.innerText.includes(t); }",
                arg=expected)

        # --- open the PENDING saved run ---
        pg.click(f"tr:has-text('{pend_id}')")
        wait_badge("PENDING")
        wait_stage("Status determined: pending")
        print("pending run badge:", badge())
        print("pending stages contain 'pending':",
              "Status determined: pending" in stage_text())
        assert badge() == "PENDING"
        assert "Status determined: pending" in stage_text()

        # --- open the APPROVED saved run (switch back) ---
        pg.click(f"tr:has-text('{appr_id}')")
        wait_badge("APPROVED")
        wait_stage("Status determined: approved")
        print("approved run badge:", badge())
        print("approved stages contain 'approved':",
              "Status determined: approved" in stage_text())
        assert badge() == "APPROVED"
        assert "Status determined: approved" in stage_text()

        # --- start a NEW live run after viewing history ---
        pg.select_option("#example", "happy_path")
        pg.click("#runBtn")
        pg.wait_for_selector("#stagelist .stage")
        pg.wait_for_timeout(800)
        stages = stage_text()
        n_stages = pg.eval_on_selector_all("#stagelist .stage", "els => els.length")
        spinner_hidden = pg.eval_on_selector("#running",
                                             "el => getComputedStyle(el).display === 'none'")
        print("live run stage count:", n_stages)
        print("spinner hidden after run:", spinner_hidden)
        print("live stages contain 'approved':", "Status determined: approved" in stages)
        assert n_stages == 4
        assert spinner_hidden
        assert "Status determined: approved" in stages

        b.close()
    print("\nALL UI CHECKS PASSED")


if __name__ == "__main__":
    main()
