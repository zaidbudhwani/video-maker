"""One place that builds the Claude client, so the key and workspace rules live once."""
import os
from pathlib import Path

from .config import ROOT

# $ per million tokens (input, output), for the cost line in report.json
PRICES = {"claude-sonnet-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-haiku-4-5": (1.0, 5.0)}


def has_key():
    from dotenv import load_dotenv

    load_dotenv(Path(ROOT) / ".env")
    return os.getenv("ANTHROPIC_API_KEY", "") not in ("", "your_key_here")


QWEN_MODEL = "qwen/qwen3.8-omni-flash"
QWEN_PRICE = (0.15, 0.47)   # $ per million tokens (input, output): only the fallback when OpenRouter sends no cost


def has_openrouter_key():
    from dotenv import load_dotenv

    load_dotenv(Path(ROOT) / ".env")
    return os.getenv("OPENROUTER_API_KEY", "").strip() not in ("", "your_key_here", "your_openrouter_key_here")


def openrouter_key():
    """The key, or "". Never log or print it."""
    return os.getenv("OPENROUTER_API_KEY", "").strip() if has_openrouter_key() else ""


def client(timeout=60.0, max_retries=1):
    """None when there's no key. Keys not tied to one workspace need it named on every request."""
    if not has_key():
        return None
    from anthropic import Anthropic

    workspace = os.getenv("ANTHROPIC_WORKSPACE_ID", "").strip()
    return Anthropic(timeout=timeout, max_retries=max_retries,
                     default_headers={"anthropic-workspace-id": workspace} if workspace else None)


def cost(model, usage):
    pin, pout = PRICES.get(model, PRICES["claude-sonnet-5"])
    return round((usage.input_tokens * pin + usage.output_tokens * pout) / 1e6, 4)


def is_ai_error(exc):
    """True when the failure came from the AI service or its answer (not from ffmpeg, a missing file, our own bug)."""
    import json as _json

    root = type(exc).__module__.split(".")[0]
    return (type(exc).__name__ == "QwenError" or root in ("anthropic", "httpx", "httpcore") or isinstance(exc, _json.JSONDecodeError)
            or (isinstance(exc, (RuntimeError, ValueError)) and str(exc).startswith("Claude")))


def explain(exc, step="copy"):
    """Turn an AI failure into {'kind', 'message'} Alex can read. kind: key | credit | rate | other.

    step says what was lost: 'copy' (titles and captions) or 'match' (clips timed to her words).
    Never includes the technical text: that stays in the job log.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    if "authentication" in name or "permissiondenied" in name or status in (401, 403) or "api key" in text \
            or "invalid x-api-key" in text:
        kind = "key"
    elif status == 402 or "credit balance" in text or "billing" in text or "insufficient" in text:
        kind = "credit"
    elif "ratelimit" in name or "overloaded" in name or status in (429, 529):
        kind = "rate"
    else:
        kind = "other"
    return {"kind": kind, "message": message(kind, step)}


def message(kind, step="copy"):
    """The plain-English line for a kind of failure. step: 'copy' (titles), 'match' (clip timing) or 'both'."""
    lost = ("the clips play in your order and the titles are simpler ones built from your own words"
            if step == "both" else
            "the clips play in your order" if step == "match" else
            "the titles and captions are simpler ones built from your own words")
    why = {"key": "The AI helper couldn't sign in",
           "credit": "The AI helper is out of credit right now",
           "rate": "The AI helper was too busy",
           "other": "The AI helper wasn't reachable"}[kind]
    return f"{why}, so {lost}. The video is finished and fine to use."
