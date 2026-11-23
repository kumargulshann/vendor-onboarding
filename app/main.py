"""
FastAPI app: exposes the process + a single-page UI.

Endpoints:
- GET  /                     -> the UI (live run view + dashboard)
- POST /api/import-onboarding -> suggest editable fields from a vendor onboarding PDF
- POST /api/preview-bank-proof -> suggest banking fields from an uploaded letter
- POST /api/run             -> run a submission (JSON text evidence OR multipart file+form)
- POST /api/run/stream      -> run a submission, streaming each stage live (SSE)
- GET  /api/cases           -> cases grouped with their attempts (reviewer view)
- GET  /api/cases/{id}      -> one case with all its attempts
- GET  /api/runs            -> flat run history (masked summaries)
- DELETE /api/runs          -> clear local run history
- GET  /api/runs/{id}       -> one run (masked); 404 for unknown ids

A PDF upload is sent together with the form on Run. The server parses it once,
keeps bytes/text/full account server-side, and returns only masked evidence.
"""
from __future__ import annotations
import json
import logging
import os
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

try:
    from python_multipart.exceptions import MultipartParseError
except ImportError:  # Older python-multipart releases export the original package name.
    from multipart.exceptions import MultipartParseError

from .models import VendorSubmission, parse_bank_letter
from .form_import import import_onboarding_form
from .runner import (run_process, list_runs, get_run, list_cases, get_case,
                     clear_run_history, CorrectionConflict)
from .pdf_evidence import extract_pdf_text, MAX_PDF_BYTES, MAX_PDF_TEXT_CHARS, PdfEvidenceError

BASE = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = BASE / "data"
app = FastAPI(title="Vendor Onboarding — Automated Review")
logger = logging.getLogger(__name__)
MAX_TEXT_REQUEST_BYTES = 320_000

_FORM_FIELDS = frozenset(VendorSubmission.__dataclass_fields__) - {
    "documents", "bank_evidence_source",
}


class SubmissionError(Exception):
    """A submission could not be read. No case or attempt was created."""


async def _bounded_body(request: Request, limit: int, error: str) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise SubmissionError(error)
        body.extend(chunk)
    return bytes(body)


def _multipart_request(request: Request, body: bytes) -> Request:
    async def receive_body():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(request.scope, receive_body)


def _make_submission(data, pdf_text=None):
    if not isinstance(data, dict):
        raise SubmissionError("Provide the submission as an object.")
    fields = {}
    for key in _FORM_FIELDS:
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            raise SubmissionError(f"{key.replace('_', ' ').capitalize()} must be text.")
        limit = MAX_PDF_TEXT_CHARS if key == "bank_proof_text" else 500
        if value is not None and len(value) > limit:
            raise SubmissionError(f"{key.replace('_', ' ').capitalize()} is too long.")
        fields[key] = value.strip() if value is not None else None
    documents = data.get("documents")
    if documents is None:
        documents = []
    if not isinstance(documents, list) or any(not isinstance(d, str) for d in documents):
        raise SubmissionError("Documents must be a list of document names.")
    fields["documents"] = documents
    fields["vendor_name"] = fields["vendor_name"] or ""
    if pdf_text is not None:
        fields["bank_proof_text"] = pdf_text
        fields["bank_evidence_source"] = "pdf"
    return VendorSubmission(**fields)


async def _submission_from_request(request: Request) -> VendorSubmission:
    """
    Build a VendorSubmission from either a JSON body (text evidence) or a
    multipart upload (form fields + optional PDF file). When a PDF is present
    it is parsed ONCE here and becomes the bank evidence; the extracted text
    and bytes never leave the server.
    """
    multipart = request.headers.get("content-type", "").lower().startswith("multipart/form-data")
    limit = MAX_TEXT_REQUEST_BYTES + (MAX_PDF_BYTES if multipart else 0)
    body = await _bounded_body(request, limit, "The submission is too large. Upload a smaller bank letter.")

    if multipart:
        bounded_request = _multipart_request(request, body)
        try:
            async with bounded_request.form(max_files=1, max_fields=32) as form:
                data = {key: form.get(key) for key in _FORM_FIELDS}
                documents = form.getlist("documents")
                if len(documents) == 1 and isinstance(documents[0], str):
                    documents = [item.strip() for item in documents[0].split(",") if item.strip()]
                data["documents"] = documents
                if "file" not in form:
                    return _make_submission(data)
                upload = form["file"]
                if not isinstance(upload, UploadFile) or not upload.filename:
                    raise SubmissionError("Choose one bank letter PDF and try again.")
                content = await upload.read(MAX_PDF_BYTES + 1)
            evidence = await run_in_threadpool(extract_pdf_text, content)
            return _make_submission(data, pdf_text=evidence["text"])
        except (HTTPException, MultipartParseError) as error:
            raise SubmissionError("The upload could not be read. Submit one bank letter PDF.") from error

    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError) as error:
        raise SubmissionError("The submission must be valid JSON.") from error
    return _make_submission(payload.get("data") if isinstance(payload, dict) else None)


@app.get("/")
def index():
    return FileResponse(BASE / "templates" / "index.html")


@app.get("/api/examples")
def api_examples():
    """Return the prepared demo fixtures so the UI can offer a Load example."""
    examples = []
    for p in sorted(EXAMPLES_DIR.glob("*.json")):
        if p.name.startswith("happy") or p.name.startswith("edge"):
            examples.append({"name": p.stem, "data": json.loads(p.read_text(encoding="utf-8"))})
    return {"examples": examples}


async def _pdf_preview(request: Request, document: str, parse):
    headers = {"Cache-Control": "no-store"}
    try:
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            raise SubmissionError(f"Choose one {document} PDF.")
        body = await _bounded_body(
            request, MAX_PDF_BYTES + 16_384, f"Upload a PDF for the {document} that is 1 MB or smaller.",
        )
        async with _multipart_request(request, body).form(max_files=1, max_fields=0) as form:
            upload = form.get("file")
            if not isinstance(upload, UploadFile) or not upload.filename:
                raise SubmissionError(f"Choose one {document} PDF.")
            content = await upload.read(MAX_PDF_BYTES + 1)
        result = await run_in_threadpool(parse, content)
    except (HTTPException, MultipartParseError):
        return JSONResponse({"error": "The form upload could not be read. Choose one PDF."},
                            status_code=400, headers=headers)
    except (PdfEvidenceError, SubmissionError) as error:
        return JSONResponse({"error": str(error)}, status_code=400, headers=headers)
    return JSONResponse(result, headers=headers)


@app.post("/api/import-onboarding")
async def api_import_onboarding(request: Request):
    return await _pdf_preview(request, "vendor onboarding form", import_onboarding_form)


def _bank_proof_fields(content: bytes) -> dict:
    evidence = parse_bank_letter(extract_pdf_text(content)["text"])
    if evidence["kind"] == "vendor_declaration":
        raise PdfEvidenceError("This is an onboarding form. Choose a separate bank letter or statement.")
    if evidence["account_conflict"]:
        raise PdfEvidenceError("The bank proof contains conflicting account numbers.")
    account = evidence["account"]
    if not evidence["holder"] or not account or not 5 <= len(account) <= 500:
        raise PdfEvidenceError("A readable account holder and one complete account number are required.")
    if evidence["ifsc_listed"] and not evidence["ifsc"]:
        raise PdfEvidenceError("The bank proof has conflicting or unreadable IFSC information.")
    return {"bank_account_number": account, "bank_routing": evidence["ifsc"]}


@app.post("/api/preview-bank-proof")
async def api_preview_bank_proof(request: Request):
    return await _pdf_preview(request, "bank letter", _bank_proof_fields)


@app.post("/api/run")
async def api_run(request: Request):
    try:
        sub = await _submission_from_request(request)
    except (PdfEvidenceError, SubmissionError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        return await run_in_threadpool(run_process, sub)
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
    except (PdfEvidenceError, SubmissionError) as e:
        err_msg = str(e)
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
        except Exception as e:
            logger.error("Run failed (%s)", type(e).__name__)
            q.put({"type": "error", "data": "The run failed. Please try again."})
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


def _local_history_access(request: Request) -> bool:
    """Allow deletion only in the local demo, including the URL seen by the browser."""
    return (
        os.environ.get("VERCEL") != "1"
        and request.client is not None
        and request.client.host in {"127.0.0.1", "::1"}
        and request.url.hostname in {"127.0.0.1", "::1", "localhost"}
    )


@app.get("/api/cases")
def api_cases(request: Request):
    cases = list_cases()
    return {"count": len(cases), "cases": cases, "can_clear_history": _local_history_access(request)}


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


@app.delete("/api/runs")
def api_clear_runs(request: Request):
    origin = f"{request.url.scheme}://{request.url.netloc}"
    if not _local_history_access(request) or request.headers.get("origin") != origin:
        return JSONResponse({"error": "Run history can only be cleared from the local dashboard."},
                            status_code=403)
    deleted, retained = clear_run_history()
    if retained:
        return JSONResponse({
            "deleted": deleted,
            "error": f"{retained} saved run(s) could not be removed. Check file access and try again.",
        }, status_code=500)
    return {"deleted": deleted}


@app.get("/api/runs/{run_id}")
def api_run_one(run_id: str):
    r = get_run(run_id)
    if r is None:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    return r
