# Five-minute demo cue sheet

Time the spoken walkthrough before recording. Aim for 4 minutes 30 seconds
to leave a buffer within the five-minute limit.

## Setup (before recording)

Open PowerShell in the project folder and start the app with an empty history
directory on port 8001. Use a fresh directory for each take.

```powershell
$env:VENDOR_ONBOARDING_RUN_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("vendor-onboarding-demo-" + [guid]::NewGuid())
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Open [the local demo](http://127.0.0.1:8001). Use the supplied synthetic examples
and the file `data/pdf/pdf_two_page_match.pdf`.

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
