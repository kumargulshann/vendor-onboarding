# Vendor onboarding edge-case test report

Four scenarios test how the review handles conflicting payment evidence,
document provenance, an incomplete correction, and two reviewers working from
the same attempt. All company and banking details are fictional.

| Case | Expected outcome | Observed in Chrome |
| --- | --- | --- |
| 1. Current and archived accounts in one packet | Pending for conflicting accounts, then Approved with one current letter | **Pass.** Two saved attempts, Pending → Approved |
| 2. A renamed declaration takes precedence over matching pasted evidence | Pending for the wrong document, then Approved with a bank letter | **Pass.** The PDF was used for the decision. Two saved attempts, Pending → Approved |
| 3. Branch migration and a blank IFSC on correction | Pending for mismatch, still Pending with a blank IFSC, then Approved after both sides match | **Pass.** Three saved attempts, Pending → Pending → Approved |
| 4. Two reviewers correct the same case | Reject the stale save, preserve the latest Pending attempt, then approve a correction from that attempt | **Pass.** The stale save added zero attempts. The fresh correction was saved as attempt 3 |

## Setup

From the project folder, start the app in PowerShell with a clean test history.

```powershell
$env:VENDOR_ONBOARDING_RUN_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("vendor-onboarding-edge-" + [guid]::NewGuid())
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

1. Open the [local app](http://127.0.0.1:8001). For each case, import the
   [Juniper onboarding form](data/pdf/vendor_onboarding_sample.pdf).
   Click **Read form PDF**, then **Apply to new submission**.
2. Mark the four document boxes unless the case says otherwise. The checklist
   simulates the presence of supporting documents. The app reads and compares the
   bank evidence separately.
3. Upload the case’s PDF under **Bank-proof PDF upload** and click **Run process**.

When correcting a case, click **Correct and resubmit** on the latest attempt,
re-enter account `001234567890`, and attach the specified evidence. Use current
IFSC `DEMO0001234` unless the test calls for a blank IFSC.

For a happy-path comparison, run the same form with all four boxes checked
and the [current bank confirmation](output/pdf/juniper_bank_account_confirmation.pdf).
The reference run was **Approved** in Chrome, with both the account and IFSC
marked **Match**.

## 1. A payment-account change leaves two confirmations in one packet

During an account transition, a vendor uploads a PDF packet with the current
confirmation on page 1 and an archived confirmation on page 2. The holder and
IFSC agree across the packet. The current account matches the declaration, and
the archived page points to a different payment destination.

1. Run the [two-confirmation packet](output/pdf/juniper_conflicting_bank_confirmations.pdf).
2. Correct the same case with the [current bank confirmation](output/pdf/juniper_bank_account_confirmation.pdf).

**Expected outcome**

Attempt 1 is **Pending**. The bank evidence shows **Conflicting account numbers**,
and the vendor response requests a current letter showing one intended payment
account. Attempt 2 is **Approved** when the standalone confirmation resolves
the contradiction.

**Observed outcome**

The holder matched on attempt 1, and the account comparison was blocked by
conflicting numbers. Neither full account number appeared in the saved result.
Attempt 2 was **Approved**, and the reviewer comparison recorded that the
submitted account matched the new evidence.

A matching page cannot establish a single payment destination when another
page contains a different account number.

## 2. A vendor declaration is renamed to look like independent bank proof

The intake form contains the right payment details. A supporting file named
like a bank confirmation contains a copy of that same declaration. Matching
bank details are also present in the pasted-text field.

1. Paste the following fictional confirmation text into **Bank-proof document**.

   ```text
   Account holder: Juniper Office Systems Pvt Ltd
   Account number: 001234567890
   IFSC: DEMO0001234
   ```

2. Make a copy of the [Juniper onboarding form](data/pdf/vendor_onboarding_sample.pdf)
   named `Juniper_Bank_Confirmation.pdf`. Upload it as bank proof and run.
3. Correct the case using the [current bank confirmation](output/pdf/juniper_bank_account_confirmation.pdf).

**Expected outcome**

The uploaded PDF takes precedence over the pasted text. The app identifies its
contents as an onboarding form, holds the case, and asks for a separate bank
letter or statement. The correction is **Approved**.

**Observed outcome**

Attempt 1 was **Pending** and showed **Wrong document**. The vendor response
requested independent bank evidence as the only action. Attempt 2 was
**Approved** with the separate confirmation.

Values copied from the declaration provide no independent corroboration. The
review checks the contents of the selected evidence.

## 3. A branch migration changes the IFSC while the account stays the same

The declaration uses the current IFSC, and an older bank confirmation lists
the previous branch code. Both sources identify the same payment account.

1. Run the [confirmation with the old IFSC](output/pdf/juniper_bank_ifsc_mismatch.pdf).
2. Correct the case with the [current bank confirmation](output/pdf/juniper_bank_account_confirmation.pdf).
   Re-enter the account number and leave the form’s IFSC blank.
3. Correct again with the current confirmation and enter `DEMO0001234`.

**Expected outcome**

Attempt 1 is **Pending** because the IFSC differs even though the account
matches. Attempt 2 remains **Pending** because the IFSC comparison still
requires a readable code on both sides. Attempt 3 is **Approved**.

**Observed outcome**

The account matched on all three attempts. The IFSC went from **Mismatch** to
**Not comparable** to **Match**. The dashboard showed
**Pending → Pending → Approved**, and the final comparison identified the
resolved IFSC check.

A known routing discrepancy stays open until the reviewer can verify both
sides. Omitting a field during correction leaves the hold in place.

## 4. Two reviewers open the same correction and one saves first

One reviewer has updated bank evidence. A second reviewer has the updated
evidence and has received address proof. Both open the correction from
attempt 1, before either saves.

1. In tab A, leave **Address proof** unchecked and run the
   [confirmation with the old account](output/pdf/juniper_bank_account_mismatch.pdf).
2. Open the same case and attempt in tab B. Click **Correct and resubmit** in
   both tabs. Re-enter the full account and current IFSC in each tab.
3. In tab A, keep **Address proof** unchecked. Attach the
   [current bank confirmation](output/pdf/juniper_bank_account_confirmation.pdf)
   and run. This produces attempt 2.
4. In tab B, check **Address proof**, attach the current bank confirmation,
   and run from the correction that was opened against attempt 1.
5. In tab B, click **Open latest attempt (2)**, then **Correct and resubmit**.
   Re-enter the account and IFSC, check **Address proof**, and reattach the
   current bank confirmation. Run to save the final correction.

**Expected outcome**

Attempt 1 is **Pending** for the account discrepancy and missing address
proof. Attempt 2 clears the bank discrepancy and stays **Pending** for address
proof alone. The old tab receives a save conflict and is directed to attempt 2.
The retry from attempt 2 becomes attempt 3 and is **Approved**.

**Observed outcome**

After tab B tried to save from attempt 1, Chrome showed **This attempt was not
saved** and **Open latest attempt (2)**. The dashboard still contained exactly
two attempts. The fresh correction was saved as attempt 3, linked to attempt 2,
and marked **Approved**.

Each correction must refer to the latest saved attempt. This preserves the
review sequence even when a stale draft contains enough information to pass
the checks.

## Verification

All four scenarios ran in Chrome with the built-in explanation and temporary
run storage. Verification covered the visible status, the vendor action, and
the saved case history after each correction. Each scenario also has an
automated API regression test.

```powershell
python -m pytest -q -k interview
```

For the five-minute recording, show the happy path and case 1 through correction.
The two-tab scenario is suited to the live interview, where the dashboard can
show the attempt count before and after the stale save.
