# Five-minute demo cue sheet

Time the spoken walkthrough before recording. Aim for 4 minutes 30 seconds
to leave a buffer within the five-minute limit.

## Setup (before recording)

Open PowerShell in the project folder and start the app with an empty history
directory on port 8001. Use a fresh directory for each take.

```powershell
$env:VENDOR_ONBOARDING_RUN_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("vendor-onboarding-demo-" + [guid]::NewGuid())

```

Open [the local demo](http://127.0.0.1:8001). Use the supplied synthetic examples
and the file `data/pdf/pdf_two_page_match.pdf`.

The [four-case test report](EDGE_CASES.md) includes the PDF fixtures, expected
and observed outcomes, and the recommended recording sequence.

### Optional Gemini explanations

In the project folder, create `.env` from [the template](.env.example) if
needed. Paste your [Google AI Studio API key](https://aistudio.google.com/apikey)
after `GEMINI_API_KEY=`. Git ignores the local file. The app selects
`gemini-3.5-flash-lite` for explanations.

If you set a key in PowerShell earlier, open a fresh terminal or run
`Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue` so the app
reads the key from the file. Restart the server, run a sample, and look for
**Gemini summary generated from validation checks** in the live run view.
The model receives the decision and specific findings, such as an IFSC
mismatch or missing address proof. Submitted values and document text stay in
the app. The rules determine the status and vendor actions. If Gemini fails
or skips the required finding, the built-in explanation uses the same facts.

On the local dashboard, **Clear run history** permanently removes saved runs
after confirmation. Use it between rehearsals when you want a clean dashboard.

## Sample inputs
| Step | Load example | What it is |
|------|--------------|------------|
| 1 | `happy_path` | A fully valid submission → clean approval. |
| 2 | `edge_account_mismatch` | Looks complete; account holder matches, but the **payment account conflicts** with the bank letter → held (pending). |
| 3 | correct step 2 | Same case, corrected account number (`1234567890`) + bank-letter PDF → approval. |

Use the **Load example** dropdown to fill the form. For step 3, click
**Correct and resubmit** (the form is restored), fix the account, attach the
bank-letter PDF, then **Run**.

## Click order
1. **Load example** → `happy_path` → **Run** → *APPROVED*.
2. **Load example** → `edge_account_mismatch` → **Run** → *PENDING*. Point out
   the **masked discrepancy** (account endings shown, full number hidden) and
   the **vendor instruction** ("does NOT match the account in the bank letter").
3. Click **Correct and resubmit** → fix `bank_account_number` to `1234567890`
   → attach the bank-letter PDF → **Run** → *APPROVED*. The comparison block
   shows "Attempt 2 was approved… the submitted account matched the new
   evidence."
4. **Reopen attempt 1** using its dashboard row → *PENDING* — explain why
   it was held (account mismatch).
5. **Reopen attempt 2** → *APPROVED* — explain what changed and what evidence
   justified approval.

## Three buyer-facing explanations

1. **Why the payment was held.**
   "The form looked complete and the account-holder name matched. The submitted
   account differed from the number on the supplied bank letter, so the app held
   the submission for correction."

2. **What the vendor supplied.**
   "The vendor corrected the account number to match the bank letter and
   resubmitted with the bank-letter PDF as evidence. That resolved the
   discrepancy — the comparison shows the account now matches the new evidence."

3. **What evidence justified approval.**
   "The submitted account number matched the number on the supplied bank
   letter. The holder matched the registered name, and the document checklist
   was complete. The decision panel shows the comparison with masked account
   endings. This decision measures consistency with the supplied evidence."

## Post-rehearsal check (human, if someone unfamiliar is available)
Ask these three questions after the walkthrough and note where they hesitated:
1. "Why was the payment held?" — hesitation here suggests the masked
   discrepancy / vendor instruction need more emphasis.
2. "What did the vendor supply to fix it?" — hesitation suggests the
   correction flow needs more narration.
3. "What evidence justified approval?" — hesitation suggests the bank-letter
   evidence and masked comparison need more explanation.

Record any hesitation points here:
- Q1: ____________________
- Q2: ____________________
- Q3: ____________________

## Possible interruptions to the recording
- **Stale code:** if the app was edited but not restarted, the browser may show
  old behaviour. Always restart before recording.
- **Demo history:** use a fresh directory from the setup above for each take.
- **PDF upload path:** step 3 attaches a real bank-letter PDF; make sure the
  file is present (`data/pdf/pdf_two_page_match.pdf`) or the upload guard will
  block the run.

## Optional PDF intake walkthrough

1. In **Import vendor onboarding form**, choose
   `data/pdf/supplier_registration_v2.pdf` and click **Read form PDF**.
   The preview should show eight values with their source pages. The bank
   account ends in `6809`, and **Show full account for review** reveals the
   leading zeros. Click **Apply to new submission** to copy the values.
2. Read and apply `data/pdf/vendor_onboarding_needs_review.pdf`. Two pages
   give different account numbers. The field stays blank and **Run process**
   remains disabled until the reviewer enters the verified account number.
3. With local OCR available, read `data/pdf/supplier_registration_scan.pdf`.
   Compare the suggestions with the scan and check the confirmation box to
   enable **Apply to new submission**.

OCR uses Tesseract with its English language data and Poppler's `pdftoppm`.
Put both executables on PATH before starting the server. For executables
outside PATH, set `ONBOARDING_TESSERACT` and `ONBOARDING_PDFTOPPM` to their
full paths. Set `TESSDATA_PREFIX` to the folder containing `eng.traineddata`
if Tesseract cannot find it. The scan limit is four pages and 1 MB.
