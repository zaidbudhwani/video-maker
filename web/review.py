"""Small helpers for the result and upload pages: the trust line, saved settings in plain words, the review timer
and the first-run tips. Everything here reads files the pipeline or the website already saved; nothing is guessed and
nothing is sent anywhere.

- trust_line(job_dir, report): conservative availability estimates from saved AI scores, never verified shown matches.
- saved_words(prefs): her remembered settings (settings.REMEMBER_KEYS + caption fixes) as plain lines.
- add_review_time / review_minutes: the "Time my review" stopwatch, one small file per video (out/<job>.review.json).
- tips_seen / mark_tips_seen: the three first-run tips, shown once per account (out/tips_seen.json).
"""
import json
import math
import re
import threading
from datetime import datetime
from pathlib import Path

REVIEW_MAX_SECONDS = 4 * 3600   # one timed session longer than this is a timer left running, not a review
_lock = threading.Lock()


# ---------- the trust line ----------
def _talked_about(texts, times):
    """Indexes of the phrases that are 'things she talks about': the same filter the not-shown panel uses
    (3+ words, not a greeting or a call to action, not an opinion), without its 'dwell 2 s' rule."""
    from video_tool import match
    keep = []
    for j, text in enumerate(texts):
        t = (text or "").strip()
        if len(t.split()) < 3 or match.FILLER_PHRASE.search(t) or match.ABSTRACT.search(t):
            continue
        keep.append(j)
    return keep


def trust_line(job_dir, report, own=None):
    """Saved AI scores estimate available footage, not the accuracy of the shown timeline."""
    if not isinstance(report, dict) or report.get("mode") != "voice":
        return None
    cm = report.get("clip_match") or {}
    settings = {**(report.get("settings") or {}), **(own or {})}
    fallback = {"kind": "none", "estimated_available": None, "estimated_missing": None,
                "text": "Your clips follow their order and available length. Clip matching wasn't used; please check the shots yourself."}
    if settings.get("privacy") == "private":
        return {**fallback, "text": "You asked me to keep your pictures private. Your clips follow their order and available length; please check the shots yourself."}
    if settings.get("match_clips") is False:
        return fallback
    try:
        data = json.loads((Path(job_dir) / "match" / "scores.json").read_text(encoding="utf-8"))
        texts, scores = data["phrases"], data["scores"]
        if not isinstance(texts, list) or not texts or not all(isinstance(t, str) for t in texts) or not isinstance(scores, list) or not cm:
            raise ValueError("scores")
        best = [-1.0] * len(texts)
        for clip in scores:
            if not isinstance(clip, list):
                raise ValueError("clip")
            for row in clip:
                if not isinstance(row, list) or len(row) != len(texts):
                    raise ValueError("row")
                if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= 10 for x in row):
                    raise ValueError("score")
                best = [max(b, x) for b, x in zip(best, row)]
        idx = _talked_about(texts, data.get("phrase_times"))
        if not idx or any(best[j] < 0 for j in idx):
            raise ValueError("incomplete scores")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        if cm.get("used"):
            return {**fallback, "text": "Saved matching information is unavailable. Please check the shots yourself."}
        return fallback
    from video_tool import match
    missing = sum(best[j] < match.NOT_SHOWN_BELOW for j in idx)
    text = ("Saved AI scores suggest footage may cover some of what you describe. "
            "This estimates available footage, not whether the finished video shows the right shots. Please check it yourself.")
    if missing == len(idx):
        text = "Saved AI scores did not identify clear supporting footage. This is an estimate, not proof that footage is missing. Please check the shots yourself."
    if not cm.get("used"):
        text += " Clip matching wasn't used; your clips follow their order and available length."
    return {"kind": "estimate", "estimated_available": len(idx) - missing,
            "estimated_missing": missing, "text": text}


# ---------- remembered settings in plain words ----------
def saved_words(prefs):
    """Her remembered choices as plain lines. Only what differs from the built-in normal settings."""
    from video_tool import settings as S
    d, p, out = S.DEFAULTS, prefs or {}, []

    def changed(k):
        return k in p and p[k] != d.get(k)

    if changed("captions_on") and p["captions_on"] is False:
        out.append("Captions off")
    if changed("caption_size"):
        out.append("Bigger captions" if p["caption_size"] > d["caption_size"] else "Smaller captions")
    if changed("caption_margin"):
        out.append("Captions higher up" if p["caption_margin"] > d["caption_margin"] else "Captions lower down")
    if changed("pause_gap"):
        out.append("More pauses cut (snappier)" if p["pause_gap"] < d["pause_gap"] else "More pauses kept (more natural)")
    if changed("remove_fillers") and p["remove_fillers"] is False:
        out.append('Keep the "um"s and "uh"s')
    if changed("skip_dead_start") and p["skip_dead_start"] is False:
        out.append("Keep the start of each clip")
    if changed("stabilize") and p["stabilize"] is False:
        out.append("Always start each clip at the beginning")
    if changed("framing"):
        out.append({"fit": "Show the whole frame", "fill": "Fill the screen, centered crop"}.get(p["framing"],
                                                                                              "Fill the screen, centered on me"))
    if changed("denoise") and p["denoise"] is True:
        out.append("Remove background noise")
    if changed("music_on") and p["music_on"] is False:
        out.append("No music")
    elif changed("music_db"):
        out.append("Louder music" if p["music_db"] > d["music_db"] else "Quieter music")
    fixes = p.get("caption_fixes") or {}
    for wrong, right in list(fixes.items())[:3]:
        out.append(f'Spell "{wrong}" as "{right}" in captions')
    if len(fixes) > 3:
        out.append(f"{len(fixes) - 3} more word fix{'es' if len(fixes) - 3 > 1 else ''} for captions")
    return out


# ---------- the review timer ----------
def _review_file(out_dir, job_id):
    return Path(out_dir) / f"{job_id}.review.json"   # starts with '<job>.': deleted with the video


def add_review_time(out_dir, job_id, seconds, user, session_id=None):
    """One timed session. Returns the video's total timed minutes. Raises ValueError on a bad number."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not (1 <= seconds <= REVIEW_MAX_SECONDS):
        raise ValueError("seconds")
    if session_id is not None and (not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id)):
        raise ValueError("session")
    f = _review_file(out_dir, job_id)
    with _lock:
        try:
            sessions = json.loads(f.read_text(encoding="utf-8")).get("sessions", [])
        except FileNotFoundError:
            sessions = []
        except (OSError, ValueError, AttributeError) as exc:
            raise OSError("Could not read saved sessions") from exc
        if not isinstance(sessions, list):
            raise OSError("Invalid saved sessions")
        if session_id and any(isinstance(s, dict) and s.get("session_id") == session_id for s in sessions):
            return review_minutes(out_dir, job_id)
        session = {"seconds": int(round(seconds)), "user": user, "ended": datetime.now().isoformat(timespec="seconds")}
        if session_id:
            session["session_id"] = session_id
        sessions.append(session)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
        tmp.replace(f)
    return review_minutes(out_dir, job_id)


def review_minutes(out_dir, job_id):
    """Total timed review minutes for one version, or None when she never timed it."""
    try:
        sessions = json.loads(_review_file(out_dir, job_id).read_text(encoding="utf-8")).get("sessions", [])
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(sessions, list):
        return None
    total = sum(s.get("seconds", 0) for s in sessions if isinstance(s, dict) and isinstance(s.get("seconds"), (int, float)))
    return total / 60 if total > 0 else None


# ---------- first-run tips ----------
def _tips_file(out_dir):
    return Path(out_dir) / "tips_seen.json"


def tips_seen(out_dir, user):
    try:
        return user in json.loads(_tips_file(out_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False


def mark_tips_seen(out_dir, user):
    f = _tips_file(out_dir)
    with _lock:
        try:
            seen = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(seen, dict):
                raise OSError("Invalid saved tips")
        except FileNotFoundError:
            seen = {}
        except (OSError, ValueError) as exc:
            raise OSError("Could not read saved tips") from exc
        seen.setdefault(user, datetime.now().isoformat(timespec="seconds"))
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(seen), encoding="utf-8")
        tmp.replace(f)
