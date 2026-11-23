# Vendor Onboarding Review

I built this app for procurement teams that need to review a new vendor before
setting them up for payment. It reads an onboarding form, checks the declared
details against separate bank evidence, and shows the reviewer what passed,
what needs attention, and what the vendor can do next.

[Open the live app](https://vendoronboarding.vercel.app/) ·
[Try the four edge cases](EDGE_CASES.md) ·
[Explore the API](https://vendoronboarding.vercel.app/docs)

## Try it in the browser

1. Open the live app. Choose the [Juniper onboarding form](data/pdf/vendor_onboarding_sample.pdf)
   under **Import vendor onboarding form**. Click **Read form PDF**, review the
   suggested fields, and click **Apply to new submission**.
2. Attach the [current bank confirmation](output/pdf/juniper_bank_account_confirmation.pdf)
   under **Bank-proof PDF upload**. Check all four document boxes for this
   fictional submission, then click **Run process**. The result is **Approved**.
3. For a second case, import the same form again and check all four boxes.
   This time upload the [current and archived confirmations together](output/pdf/juniper_conflicting_bank_confirmations.pdf).
   The result is **Pending** because the packet contains two account numbers,
   even though one page agrees with the declaration.
4. Click **Correct and resubmit**, attach the single current confirmation,
   and run again. The account and IFSC from your submitted declaration are
   still available in the same tab. The dashboard shows both attempts under
   one case, and **Reviewer comparison** explains what cleared the hold.

The public demo is shared. Use the fictional fixtures in this repository.

## Features

- **PDF intake with review.** Import two supported onboarding form layouts,
  or fill in the form directly. The importer suggests up to eight fields across
  multiple pages, including vendor and legal names, country, GSTIN, contact
  email, account holder, account number, and IFSC. Each suggestion shows its
  source page. Conflicting or unreadable values require review before they can
  be used.
- **Optional OCR for scanned forms.** Image-only onboarding forms can be read
  locally with Tesseract and Poppler. The reviewer confirms the OCR results
  before applying them. Bank proof uses searchable PDFs or pasted letter text.
- **Bank evidence comparison.** The app compares the account holder with the
  vendor identity and checks the full account number across the declaration
  and bank proof, preserving leading zeros. It also checks IFSC when
  applicable. A selected PDF takes precedence over pasted text, and a vendor
  declaration uploaded in the bank-proof slot is flagged as the wrong document.
- **Review rules.** The app checks tax ID formats for India, the United States,
  the United Kingdom, and Germany. It checks the document checklist for
  incorporation proof, bank proof, tax registration, and address proof. A
  missing or invalid contact email appears as a warning. The checklist records
  document presence, while the bank letter is independently parsed for comparison.
- **Live stages and vendor follow-up.** The live view shows intake, validation,
  decision, and explanation as the run progresses. When a check blocks the
  case, the vendor-facing response specifies what to supply or correct.
- **Corrections with safeguards.** Replace evidence and retry on the same case.
  A readable replacement bank PDF can suggest an account and IFSC, which the
  reviewer can explicitly copy into the declaration. Ambiguous proof yields
  no suggestions. A stale draft points the reviewer to the latest attempt
  before another correction can be saved.
- **Dashboard and history.** Reopen saved attempts to see the result, checks,
  masked account and IFSC values, and what changed since the previous attempt.
  Local users can clear run history after confirming.

## How decisions are made

I designed the app around a rule engine so the reviewer can trace an outcome
back to specific checks. For example, a bank packet with two different account
numbers stays pending even if one of them matches the form. The app asks for
a current confirmation showing only the intended payment account.

| Decision | When it appears |
| --- | --- |
| **Approved** | All mandatory checks on the supplied information pass. |
| **Pending** | The reviewer needs a correction or more evidence, such as a current bank letter or a missing document. |
| **Rejected** | A blocking rule fails, such as an unsupported country or an invalid tax ID format. |

Gemini 3.5 Flash-Lite can turn the verified findings into a short explanation.
I send the decision and those findings, with the submitted values and document
text kept in the app. A built-in explanation covers the same findings when
Gemini is unavailable or returns a generic response. The rules determine the
status and generate the vendor's next steps.

## Run locally

Clone the repository and run these commands from the project folder with
Python 3.10 or newer.

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Open [the local app](http://127.0.0.1:8001/) or
[the local API docs](http://127.0.0.1:8001/docs).

- **Gemini.** Copy [`.env.example`](.env.example) to `.env`, set
  `GEMINI_API_KEY`, and restart the server. On Vercel, set the same key in the
  project's environment variables. An empty key uses the built-in explanation.
- **OCR.** Put Poppler's `pdftoppm` and Tesseract with its English language data
  on PATH. For custom installations, set `ONBOARDING_PDFTOPPM` and
  `ONBOARDING_TESSERACT` to the executable paths. Uploads have a 1 MB limit.
  Searchable PDFs can contain up to 20 pages, and local OCR handles up to four.
- **History.** Runs are saved as JSON under `outputs/` locally. Set
  `VENDOR_ONBOARDING_RUN_DIR` to use a separate directory for tests or demos.
  The hosted demo uses temporary storage, so its history can reset.

## Project structure

| Path | What it does |
| --- | --- |
| [`app/main.py`](app/main.py) | FastAPI routes for the app, uploads, live run events, and history |
| [`app/form_import.py`](app/form_import.py) and [`app/pdf_evidence.py`](app/pdf_evidence.py) | Field extraction, PDF parsing, and optional OCR |
| [`app/rules.py`](app/rules.py) and [`app/runner.py`](app/runner.py) | Validation, decisions, case history, and attempt comparison |
| [`app/ai_layer.py`](app/ai_layer.py) | Gemini and built-in explanations |
| [`templates/index.html`](templates/index.html) | Browser interface built with HTML, CSS, and JavaScript |
| [`data/`](data/) and [`output/pdf/`](output/pdf/) | Synthetic submissions, evaluation inputs, and demo PDFs |

## Tests

The [edge-case walkthrough](EDGE_CASES.md) includes the inputs, browser steps,
and expected and observed outcomes for four scenarios. The regression suite
covers PDF parsing, decisions, masking, explanations, and correction conflicts.

```bash
python -m pip install pytest httpx
python -m pytest -q
```
