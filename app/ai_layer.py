"""
AI reasoning layer.

Design intent (and something to speak to in the demo):
- The deterministic rule engine (rules.py) is the backbone. It always produces a
  verdict, so the process ALWAYS runs and every decision is reproducible.
- This layer adds a *credibility / consistency* narrative on top, and exposes one
  clear, optional job for a model: extracting data from a messy bank-proof
  letter. With no model wired, extraction falls back to the deterministic
  parser so the demo never breaks and approval rules stay predictable.

Decision is intentionally deterministic: the rule engine calls
`extract_bank_holder` here, but with no model wired it delegates to the
deterministic parser. If you later wire a model, unfamiliar letter formats get
the AI treatment while the approval *decision* remains rule-based and auditable.
"""
from __future__ import annotations
from typing import Callable, Optional

from .models import (extract_bank_holder as _det_extract_holder,
                     extract_bank_account as _det_extract_account,
                     normalize_name)

LLM_CALL: Optional[Callable[[str], str]] = None


def set_llm(fn: Callable[[str], str]) -> None:
    """Wire in a real model (e.g. a wrapper over Claude/GPT/Gemini)."""
    global LLM_CALL
    LLM_CALL = fn


def extract_bank_holder(text: str) -> Optional[str]:
    """Extract the account-holder name; uses the wired model when available."""
    if not text:
        return None
    if LLM_CALL is not None:
        prompt = ("You extract data from documents. From the bank letter below, "
                  "return ONLY the account-holder name, or the word NONE if it "
                  "cannot be determined.\n\n" + text[:1500])
        try:
            out = LLM_CALL(prompt).strip()
            if out and out.upper() != "NONE":
                return out[:80]
        except Exception:
            pass  # fall through to deterministic
    return _det_extract_holder(text)


def extract_bank_account(text: str) -> Optional[str]:
    """Extract the account number. Deterministic (structured data, high-stakes)."""
    return _det_extract_account(text)


def parse_bank_letter(text: str) -> dict:
    """
    Parse holder + account number from a bank letter in a single pass, using the
    LLM-capable holder extractor when a model is wired. The returned evidence is
    passed into the rules, the displayed result and the saved record so the SAME
    extraction backs every part of the decision (no double parsing).
    """
    return {
        "holder": extract_bank_holder(text),
        "account": extract_bank_account(text),
    }


def _fallback_reasoning(rule) -> str:
    if rule.passed:
        return ("All mandatory checks cleared. Identity, tax-ID format, banking "
                "and document completeness are consistent; no blocking "
                "discrepancies found.")
    # List the failed check NAMES and point to the vendor-facing response for
    # the action, rather than restating each check's detail (which duplicates
    # the vendor action and would show it more than once).
    names = ", ".join(c.name for c in rule.errors)
    return (f"Decision held because the following mandatory checks failed: "
            f"{names}. See the vendor-facing response for the action needed.")


def reason(rule) -> dict:
    """Produce a structured reasoning trace for the run view / dashboard."""
    base = _fallback_reasoning(rule)
    used_ai = False
    narrative = base
    if LLM_CALL is not None:
        from .rules import RuleResult  # local import avoids a circular import
        sub_summary = getattr(rule, "_submission_summary", None) or {}
        prompt = (
            "You are a procurement compliance analyst. Given a vendor submission "
            "and these validation checks, produce a concise 3-4 sentence "
            "credibility summary and the single most important risk to flag.\n\n"
            f"Submission: {sub_summary}\n"
            f"Checks: {[(c.name, c.passed, c.detail) for c in rule.checks]}"
        )
        try:
            narrative = LLM_CALL(prompt).strip() or base
            used_ai = True
        except Exception as e:  # never let the AI break the run
            narrative = f"{base}\n(AI layer unavailable, used deterministic reasoning: {e})"
    return {"narrative": narrative, "used_ai": used_ai}
