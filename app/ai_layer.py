"""
AI reasoning layer.

Design intent (and something to speak to in the demo):
- The deterministic rule engine (rules.py) is the backbone. It always produces a
  verdict, so the process ALWAYS runs and every decision is reproducible.
- Bank proof extraction stays local. The model sees safe findings written by
  the rules, without submitted values or excerpts from documents.
"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Callable, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from .models import parse_bank_letter

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_TIMEOUT = 8
LLM_CALL: Optional[Callable[[str], str]] = None


def set_llm(fn: Callable[[str], str]) -> None:
    """Wire in a real model (e.g. a wrapper over Claude/GPT/Gemini)."""
    global LLM_CALL
    LLM_CALL = fn


def _gemini_summary(prompt: str, key: str) -> str:
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 256,
            "thinkingConfig": {"thinkingLevel": "MINIMAL"},
        },
    }
    request = Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    request.add_unredirected_header("x-goog-api-key", key)
    with urlopen(request, timeout=GEMINI_TIMEOUT) as response:
        result = json.loads(response.read(65_536))
    candidates = result.get("candidates") or []
    if not candidates or candidates[0].get("finishReason") != "STOP":
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return " ".join(
        part["text"] for part in parts
        if not part.get("thought") and isinstance(part.get("text"), str)
    )


def reason(rule, *, status: str | None = None, actionable_checks: set[str] | None = None) -> dict:
    """Explain the decision from findings that the validation rules can share."""
    blocking_checks = [
        check for check in rule.reject_errors + rule.pending_errors
        if check.explanation and (actionable_checks is None or check.name in actionable_checks)
    ]
    blockers = [check.explanation for check in blocking_checks]
    passed = {check.name: check.explanation for check in rule.checks if check.passed and check.explanation}
    if rule.passed:
        supporting_checks = ("bank_account_match", "bank_ifsc_match", "tax_id_format", "documents_complete")
    else:
        supporting_checks = ("bank_account_match",) if any(
            check.name == "bank_ifsc_match" for check in blocking_checks
        ) else ()
    supporting = [passed[name] for name in supporting_checks if name in passed]
    warnings = [check.explanation for check in rule.warnings if check.explanation]
    findings = blockers + supporting
    if not findings:
        findings = ["The required checks cleared." if rule.passed else "A required check failed."]
    opening = findings[0]
    base = " ".join(findings + [f"Warning: {warning}" for warning in warnings])
    callback = LLM_CALL
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if callback is None and not key:
        return {"narrative": base, "used_ai": False, "ai_attempted": False, "provider": None}

    provider = "Gemini" if callback is None else "AI"
    context = {
        "decision": status,
        "required_opening_sentence": opening,
        "blocking_findings": blockers,
        "supporting_findings": supporting,
        "warnings_that_do_not_block": warnings,
    }
    prompt = (
        "Write a concise decision explanation for a vendor onboarding reviewer. "
        "Begin with the required opening sentence exactly as supplied, with no title or bullets. "
        "Use up to two more sentences to cover any other blocking findings. "
        "When supplied, connect supporting findings to the decision and label warnings separately. "
        "Use only these facts. Do not invent causes, repeat the status as a headline, recommend "
        "payment, or prescribe fixes. The app shows the vendor's next steps separately.\n"
        + json.dumps(context)
    )
    try:
        reply = (callback(prompt) if callback is not None else _gemini_summary(prompt, key)).strip()
        if reply.startswith(opening) and len(reply) <= 900:
            return {"narrative": reply, "used_ai": True, "ai_attempted": True, "provider": provider}
        logger.warning("%s returned no usable decision explanation", provider)
    except HTTPError as error:
        logger.warning("%s explanation request returned HTTP %s", provider, error.code)
        error.close()
    except Exception as error:
        logger.warning("%s explanation unavailable (%s)", provider, type(error).__name__)
    return {"narrative": base, "used_ai": False, "ai_attempted": True, "provider": provider}
