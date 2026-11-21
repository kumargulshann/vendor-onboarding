"""
FastAPI app: exposes the process + a single-page UI.

Endpoints:
- GET  /                     -> the UI (live run view + dashboard)
- POST /api/run             -> run a submission (JSON text evidence OR multipart file+form)
- POST /api/run/stream      -> run a submission, streaming each stage live (SSE)
- GET  /api/cases           -> cases grouped with their attempts (reviewer view)
- GET  /api/cases/{id}      -> one case with all its attempts
- GET  /api/runs            -> flat run history (masked summaries)
- GET  /api/runs/{id}       -> one run (masked); 404 for unknown ids

A PDF upload is sent together with the form on Run. The server parses it once,
keeps bytes/text/full account server-side, and returns only masked evidence.
"""
from __future__ import annotations
import json
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .models import VendorSubmission
from .runner import run_process, list_runs, get_run, list_cases, get_case, CorrectionConflict
from .pdf_evidence import extract_pdf_text, PdfEvidenceError

BASE = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = BASE / "data"
app = FastAPI(title="Vendor Onboarding — Automated Review")

_FORM_FIELDS = ["vendor_name", "legal_name", "country", "tax_id", "contact_email",
                "bank_holder_name", "bank_account_number", "bank_proof_text",
                "bank_evidence_source", "case_id", "expected_previous_run_id"]


async def _submission_from_request(request: Request) -> VendorSubmission:
    """
    Build a VendorSubmission from either a JSON body (text evidence) or a
    multipart upload (form fields + optional PDF file). When a PDF is present
    it is parsed ONCE here and becomes the bank evidence; the extracted text
    and bytes never leave the server.
    """
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        data: dict = {}
        for k in _FORM_FIELDS:
            if k in form:
                data[k] = form.get(k)
        if "documents" in form:
            dv = form.getlist("documents")
            if len(dv) == 1 and "," in dv[0]:
                data["documents"] = [x.strip() for x in dv[0].split(",") if x.strip()]
            else:
                data["documents"] = dv
        else:
            data["documents"] = []

        files = form.getlist("file")
        file = files[0] if files else None
        if file is not None and getattr(file, "filename", ""):
            # Bounded read: read at most MAX+1 bytes so an oversized file is
            # rejected without buffering the whole upload into memory.
            from .pdf_evidence import MAX_PDF_BYTES
            content = await file.read(MAX_PDF_BYTES + 1)
            if len(content) > MAX_PDF_BYTES:
                raise PdfEvidenceError(
                    f"The uploaded PDF is too large (max "
                    f"{MAX_PDF_BYTES // 1024} KB). Please upload a smaller "
                    "bank letter.")
            ev = extract_pdf_text(content)   # raises PdfEvidenceError
            data["bank_proof_text"] = ev["text"]
            data["bank_evidence_source"] = "pdf"
        return VendorSubmission.from_dict(data)

    body = await request.json()
    return VendorSubmission.from_dict(body["data"])


@app.get("/")
def index():
    return FileResponse(BASE / "templates" / "index.html")


@app.get("/api/examples")
def api_examples():
    """Return the prepared demo fixtures so the UI can offer a Load example."""
    examples = []
    for p in sorted(EXAMPLES_DIR.glob("*.json")):
        if p.name.startswith("happy") or p.name.startswith("edge"):
            examples.append({"name": p.stem, "data": json.loads(p.read_text())})
    return {"examples": examples}


@app.post("/api/run")
async def api_run(request: Request):
    try:
        sub = await _submission_from_request(request)
    except PdfEvidenceError as e:
        return JSONResponse({"error": e.message}, status_code=400)
    try:
        return run_process(sub)
    except CorrectionConflict as e:
        return JSONResponse({
            "error": e.message,
            "conflict": {
                "case_id": e.case_id,
                "latest_run_id": e.latest_run_id,
                "latest_attempt_number": e.latest_attempt_number,
            },
        }, status_code=409)


@app.post("/api/run/stream")
async def api_run_stream(request: Request):
    """
    Run the process and stream each stage as it executes. Accepts JSON or
    multipart (file + form). On a file-level error, streams a single error
    event instead of stages.
    """
    try:
        sub = await _submission_from_request(request)
    except PdfEvidenceError as e:
        err_msg = e.message
        async def err_stream():
            yield f"event: error\ndata: {json.dumps({'error': err_msg})}\n\n"
        return StreamingResponse(err_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    q: "queue.Queue[dict]" = queue.Queue()

    def on_event(ev: dict):
        q.put(ev)

    def worker():
        try:
            result = run_process(sub, on_event=on_event)
            q.put({"type": "result", "data": result})
        except CorrectionConflict as e:
            q.put({"type": "error", "data": e.message,
                   "conflict": {"case_id": e.case_id,
                                "latest_run_id": e.latest_run_id,
                                "latest_attempt_number": e.latest_attempt_number}})
        except Exception as e:  # surface errors to the client
            q.put({"type": "error", "data": str(e)})
        finally:
            q.put({"type": "done"})

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        while True:
            ev = q.get()
            t = ev.get("type")
            if t == "done":
                break
            if t == "result":
                yield f"event: result\ndata: {json.dumps(ev['data'], default=str)}\n\n"
            elif t == "error":
                payload = {"error": ev["data"]}
                if "conflict" in ev:
                    payload["conflict"] = ev["conflict"]
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
            else:
                yield f"event: stage\ndata: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/cases")
def api_cases():
    cases = list_cases()
    return {"count": len(cases), "cases": cases}


@app.get("/api/cases/{case_id}")
def api_case_one(case_id: str):
    c = get_case(case_id)
    if c is None:
        return JSONResponse({"error": "Case not found"}, status_code=404)
    return c


@app.get("/api/runs")
def api_runs():
    runs = list_runs()
    summaries = [{
        "id": r["id"], "status": r["status"],
        "vendor": r["input"].get("vendor_name", ""),
        "duration_ms": r.get("duration_ms"),
        "reason": (r.get("reasoning", "") or "")[:120],
    } for r in runs]
    return {"count": len(summaries), "runs": summaries}


@app.get("/api/runs/{run_id}")
def api_run_one(run_id: str):
    r = get_run(run_id)
    if r is None:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    return r
