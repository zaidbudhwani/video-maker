"""Step 4: words -> 2-4 word caption lines -> .ass file (burned in by the final render)."""
import re
from pathlib import Path

from . import layout, settings
from .config import CAPTION_MAX_WORDS, CAPTION_MIN_SECONDS, CAPTION_MIN_WORDS


# Plain bold white, black outline, no animation. Bottom-centre anchored; size and
# height come from her feedback (settings), clamped out of the bottom 20%.
def style():
    size, margin, _ = layout.caption_geometry()   # vertical: caption_size, caption_margin x 1920 (unchanged)
    outline = max(4, round(size / 13))
    return (f"Style: Default,Arial,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
            f"-1,0,0,0,100,100,0,0,1,{outline},0,2,150,150,{margin},1")


def apply_fixes(words):
    """Her word corrections ("Alexe" -> "Alex"): whole words, punctuation kept."""
    fixes = settings.get("caption_fixes")
    out = []
    for w in words:
        text = w["word"]
        for wrong, right in fixes.items():
            text = re.sub(rf"(?i)(?<![\w-]){re.escape(wrong)}(?![\w-])", right, text)
        out.append({**w, "word": text})
    return out
BREAK_AFTER = (".", ",", "?", "!", ";", ":")

# Burned in during the final render with the bare filename and cwd=job_dir,
# which avoids Windows drive-letter escaping inside ffmpeg filters.


def group_lines(words):
    lines, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 0
        enough = len(cur) >= CAPTION_MIN_WORDS
        if len(cur) >= CAPTION_MAX_WORDS or (enough and (w["word"].endswith(BREAK_AFTER) or gap > 0.5)):
            lines.append(cur)
            cur = []
    if cur:
        if lines and len(cur) < CAPTION_MIN_WORDS:
            lines[-1].extend(cur)
        else:
            lines.append(cur)

    timed = []
    for i, ln in enumerate(lines):
        # stay up until the next line starts (no flicker), but don't linger in pauses
        end = lines[i + 1][0]["start"] if i + 1 < len(lines) else ln[-1]["end"] + 0.3
        end = min(end, ln[-1]["end"] + 0.6)
        timed.append({"text": " ".join(w["word"] for w in ln), "start": ln[0]["start"], "end": end})

    # Brief: any line under 1s merges into the next one
    merged = []
    for ln in timed:
        if merged and merged[-1]["end"] - merged[-1]["start"] < CAPTION_MIN_SECONDS:
            prev = merged.pop()
            ln = {"text": prev["text"] + " " + ln["text"], "start": prev["start"], "end": ln["end"]}
        merged.append(ln)
    if len(merged) > 1 and merged[-1]["end"] - merged[-1]["start"] < CAPTION_MIN_SECONDS:
        last = merged.pop()
        merged[-1] = {"text": merged[-1]["text"] + " " + last["text"],
                      "start": merged[-1]["start"], "end": last["end"]}
    for ln in merged:
        ln["end"] = max(ln["end"], ln["start"] + CAPTION_MIN_SECONDS)
    return merged


def _ts(t):
    m, s = divmod(max(t, 0), 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:05.2f}"


def rows(text):
    """A merged caption can exceed 4 words; stack it as balanced rows of <= 4."""
    w = text.split()
    n = -(-len(w) // CAPTION_MAX_WORDS)  # ceil
    size = -(-len(w) // n)
    return r"\N".join(" ".join(w[i:i + size]) for i in range(0, len(w), size))


def write_ass(words, job_dir, name="captions.ass"):
    lines = group_lines(words) if settings.get("captions_on") and words else []
    size, base, floor = layout.caption_geometry()
    width, height = layout.size()

    def margin_for(stacked):
        """A caption of 3 or more rows grows upward over the picture: sink it one row per extra row so its top edge sits
        where a two-row caption's does. Never into the bottom 20% (the platforms' own buttons and title)."""
        extra = stacked.count(r"\N") - 1
        if extra < 1:
            return 0   # 0 = the style's own margin
        return max(floor, base - round(size * 1.15) * extra)

    out = []
    for l in lines:
        stacked = rows(l["text"].replace("{", "(").replace("}", ")"))
        out.append(f"Dialogue: 0,{_ts(l['start'])},{_ts(l['end'])},Default,,0,0,{margin_for(stacked)},," + stacked)
    body = "\n".join(out)
    ass = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n\n"
           "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
           "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,"
           f"MarginL,MarginR,MarginV,Encoding\n{style()}\n\n"
           "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n" + body + "\n")
    path = Path(job_dir) / name
    path.write_text(ass, encoding="utf-8")
    return path, lines


# ---- SRT (for YouTube/Facebook caption tracks, and for people watching with the sound off) ----
def _srt_ts(t):
    t = max(float(t), 0.0)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(lines, path):
    """Write caption lines ([{'text','start','end'}], as returned by write_ass / group_lines) as an .srt file.
    Same lines, same timing as the burned-in captions. Returns the path."""
    blocks = []
    for i, ln in enumerate(lines, 1):
        text = ln["text"].replace("\n", " ").strip()
        end = max(ln["end"], ln["start"] + 0.05)
        blocks.append(f"{i}\n{_srt_ts(ln['start'])} --> {_srt_ts(end)}\n{text}\n")
    path = Path(path)
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def _ass_secs(ts):
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def read_ass_lines(ass_path):
    """The caption lines that were burned in, read back from a finished .ass file (so an SRT made later matches exactly)."""
    out = []
    for raw in Path(ass_path).read_text(encoding="utf-8").splitlines():
        if not raw.startswith("Dialogue:"):
            continue
        f = raw.split(",", 9)
        if len(f) < 10:
            continue
        out.append({"text": f[9].replace(r"\N", " ").strip(), "start": _ass_secs(f[1]), "end": _ass_secs(f[2])})
    return out
