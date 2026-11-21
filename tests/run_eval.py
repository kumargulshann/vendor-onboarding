"""
Day 5 evaluation runner: submit the 16 synthetic cases through the normal
application entry points and compare the decision + explanation with the
recorded expectations.

Run (with the app on localhost:8000):
    python3 -m tests.run_eval

Writes results to data/eval/results.json (kept for the retest label).
Reports counts + denominators for: exact status matches, correct blocking
reasons, unsafe approvals, and valid submissions unnecessarily held/rejected.
"""
from __future__ import annotations
import json
import pathlib
import sys
import urllib.request

BASE = "http://localhost:8000"
EVAL = pathlib.Path(__file__).resolve().parent.parent / "data" / "eval"
PDF_DIR = EVAL / "pdf"


def post_json(path, data):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def post_multipart(path, fields, pdf_path):
    import uuid
    boundary = "----eval" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        if isinstance(v, list):
            for item in v:
                parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                             f"name=\"{k}\"\r\n\r\n{item}\r\n".encode())
        else:
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                         f"name=\"{k}\"\r\n\r\n{v if v is not None else ''}\r\n".encode())
    pdf_bytes = PDF_DIR.joinpath(pdf_path).read_bytes()
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                 f"name=\"file\"; filename=\"{pdf_path}\"\r\n"
                 f"Content-Type: application/pdf\r\n\r\n".encode())
    parts.append(pdf_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(BASE + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.load(urllib.request.urlopen(req))


def blocking_reasons(run):
    return sorted(c["name"] for c in run.get("checks", [])
                  if not c["passed"] and c.get("severity", "error") == "error")


def vendor_actions(run):
    return [m["message"] for m in run.get("vendor_messages", [])]


def main():
    fixtures = sorted((EVAL / f"{n}.json" for n in
                       [f.stem for f in EVAL.glob("eval_*.json")
                        if f.stem != "expected"]),
                      key=lambda p: p.name)
    expected = json.loads((EVAL / "expected.json").read_text(encoding="utf-8"))

    results = []
    for fp in fixtures:
        fx = json.loads(fp.read_text(encoding="utf-8"))
        name = fx["name"]
        exp = expected[name]
        try:
            if fx.get("pdf"):
                run = post_multipart("/api/run", fx["data"], fx["pdf"])
            else:
                run = post_json("/api/run", {"data": fx["data"]})
            status = run["status"]
            reasons = blocking_reasons(run)
            actions = vendor_actions(run)
            status_ok = status == exp["status"]
            reasons_ok = reasons == sorted(exp["blocking_reasons"])
            # vendor actions: check each expected action appears (order-insensitive)
            actions_ok = all(a in actions for a in exp["vendor_actions"]) and \
                len(actions) == len(exp["vendor_actions"])
            results.append({
                "name": name, "status": status,
                "expected_status": exp["status"],
                "status_match": status_ok,
                "blocking_reasons": reasons,
                "expected_reasons": sorted(exp["blocking_reasons"]),
                "reasons_match": reasons_ok,
                "vendor_actions": actions,
                "expected_actions": exp["vendor_actions"],
                "actions_match": actions_ok,
            })
        except Exception as e:
            results.append({"name": name, "error": str(e),
                            "status_match": False, "reasons_match": False,
                            "actions_match": False})

    n = len(results)
    status_ok = sum(1 for r in results if r.get("status_match"))
    reasons_ok = sum(1 for r in results if r.get("reasons_match"))
    actions_ok = sum(1 for r in results if r.get("actions_match"))

    # unsafe approvals: expected not approved but got approved
    unsafe = [r["name"] for r in results
              if r.get("expected_status") != "approved"
              and r.get("status") == "approved"]
    # valid unnecessarily held/rejected: expected approved but got pending/rejected
    over_held = [r["name"] for r in results
                 if r.get("expected_status") == "approved"
                 and r.get("status") in ("pending", "rejected")]

    failures = [r for r in results
                if not (r.get("status_match") and r.get("reasons_match")
                        and r.get("actions_match"))]

    (EVAL / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n=== EVALUATION ({n} cases) ===")
    print(f"exact status match : {status_ok}/{n}")
    print(f"correct blocking   : {reasons_ok}/{n}")
    print(f"correct vendor act : {actions_ok}/{n}")
    print(f"unsafe approvals   : {len(unsafe)} ({unsafe or 'none'})")
    print(f"over-held/rejected : {len(over_held)} ({over_held or 'none'})")
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for r in failures:
            print(f"  {r['name']}: expected {r.get('expected_status')} / "
                  f"{r.get('expected_reasons')} -> got {r.get('status')} / "
                  f"{r.get('blocking_reasons')}")
            if not r.get("reasons_match"):
                print(f"      reasons diff: expected {r.get('expected_reasons')} "
                      f"got {r.get('blocking_reasons')}")
            if not r.get("actions_match"):
                print(f"      actions: expected {r.get('expected_actions')} "
                      f"got {r.get('vendor_actions')}")
            if "error" in r:
                print(f"      error: {r['error']}")
    else:
        print("\nALL 16 PASSED (status, blocking reasons, vendor actions)")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
