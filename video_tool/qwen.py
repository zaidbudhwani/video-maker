"""Optional second clip-matching engine: Qwen3.8-Omni-Flash through OpenRouter (experiment).

Only used when the setting match_engine is qwen/qwen_video AND an OpenRouter key exists.
Prompts are never allowed to be stored or trained on by the provider (data_collection: deny).
The key goes only in the Authorization header and never appears in any message, log or report.
"""
import json
import os
import re
import time

import httpx

from . import ai, settings

DEFAULT_BASE = "https://openrouter.ai/api/v1"
RETRIES = 2                     # extra tries for busy/temporary errors
BACKOFF = 1.5                   # seconds, doubled each try (tests shrink it)


class QwenError(Exception):
    """A failed OpenRouter call. Never carries the key."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def base_url():
    return (os.getenv("OPENROUTER_BASE_URL") or DEFAULT_BASE).rstrip("/")


def _clean(text, key):
    text = str(text)
    return text.replace(key, "[key]") if key else text


def _as_rows(obj):
    """The scores arrive as {"rows": [...]}, but Qwen sometimes answers with the bare list of rows."""
    if isinstance(obj, list) and obj and all(isinstance(r, dict) for r in obj):
        return {"rows": obj}
    return obj if isinstance(obj, dict) else None


def _first_json(text):
    """The first complete JSON object or list of rows in a reply (it may sit in prose or a code fence)."""
    dec = json.JSONDecoder()
    starts = sorted(i for i in (m.start() for m in re.finditer(r"[\[{]", text)))
    for i in starts:
        try:
            obj, _ = dec.raw_decode(text[i:])
        except ValueError:
            continue
        found = _as_rows(obj)
        if found is not None and ("rows" in found or text[i] == "{"):
            # a lone row object inside a list is part of the list: only accept it when no list wraps it
            if "rows" not in found and i > 0 and any(text[j] == "[" for j in starts if j < i):
                continue
            return found
    raise QwenError("Qwen's answer had no readable JSON")


def _cost(usage, model_prices):
    pin, pout = model_prices
    ti, to = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    c = usage.get("cost")
    return ti, to, round(float(c), 6) if isinstance(c, (int, float)) else round((ti * pin + to * pout) / 1e6, 6)


def _is_param_complaint(status, body):
    return status in (400, 404) and re.search(r"response_format|json_schema|structured|unsupported|not support|parameter",
                                              body, re.I) is not None


def chat(content_parts, schema, timeout=120.0):
    """One scoring request. Returns (parsed_json, {input_tokens, output_tokens, cost_usd}). Raises QwenError."""
    if not settings.other_ai_authorized():   # last line of defence: nothing leaves the computer for a provider she has not approved
        raise QwenError("This provider is not approved for her content", 403)
    key = ai.openrouter_key()
    if not key:
        raise QwenError("No OpenRouter key", 401)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "X-Title": "Alex Video Maker", "HTTP-Referer": "http://localhost"}
    payload = {"model": ai.QWEN_MODEL, "temperature": 0,
               "messages": [{"role": "user", "content": content_parts}],
               "response_format": {"type": "json_schema",
                                   "json_schema": {"name": "clip_scores", "strict": True, "schema": schema}},
               "provider": {"require_parameters": True, "data_collection": "deny"},
               "usage": {"include": True}}
    url = base_url() + "/chat/completions"
    dropped, tries, delay = False, 0, BACKOFF
    while True:
        try:
            r = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            status, body = r.status_code, r.text
        except httpx.HTTPError as e:
            status, body = None, f"{type(e).__name__}"
            if tries < RETRIES:
                tries += 1
                time.sleep(delay)
                delay *= 2
                continue
            raise QwenError(f"Couldn't reach OpenRouter ({_clean(body, key)})") from None
        if status == 200:
            break
        if not dropped and _is_param_complaint(status, body):
            # Optional parameter only: drop the structured-output ask. data_collection deny and the rest stay.
            dropped = True
            payload.pop("response_format")
            payload["provider"] = {"data_collection": "deny"}
            continue
        if status in (429, 500, 502, 503, 504, 529) and tries < RETRIES:
            tries += 1
            time.sleep(delay)
            delay *= 2
            continue
        raise QwenError(f"OpenRouter answered {status}: {_clean(body, key)[:200]}", status)
    try:
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        if isinstance(text, list):
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        text = str(text)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise QwenError("Qwen's answer wasn't readable") from None
    try:
        parsed = _as_rows(json.loads(text))
        if parsed is None:
            raise ValueError
    except ValueError:
        parsed = _first_json(text)
    ti, to, c = _cost(data.get("usage") if isinstance(data.get("usage"), dict) else {}, ai.QWEN_PRICE)
    return parsed, {"input_tokens": ti, "output_tokens": to, "cost_usd": c}
