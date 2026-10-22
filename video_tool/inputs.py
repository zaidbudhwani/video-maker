"""Find and validate a job folder: ordered clips + an optional voice-over.

Clips numbered 01, 02, 03... play in that order. Clips without numbers are put in the order they were filmed.

No voice-over means "talking to camera": the clips' own sound is used."""
import re
from pathlib import Path

from . import clip_order
from .config import AUDIO_EXT, VIDEO_EXT
from .errors import AlexError

ORDER_RE = re.compile(r"^(\d+)")


def find_inputs(folder):
    folder = Path(folder)
    if not folder.is_dir():
        raise AlexError("Folder not found.", folder)
    files = [f for f in folder.iterdir() if f.is_file()]
    clips = [f for f in files if f.suffix.lower() in VIDEO_EXT]
    voices = [f for f in files if f.suffix.lower() in AUDIO_EXT]

    if not clips:
        raise AlexError("No video clips found (.mp4 / .mov).", folder)
    if len(voices) > 1:
        names = ", ".join(v.name for v in voices)
        raise AlexError(f"Found {len(voices)} audio files ({names}). Use one voice-over, or none to keep the clips' own sound.", folder)
    voice = voices[0] if voices else None
    if len(clips) == 1:
        return clips, voice

    numbered = [c for c in clips if ORDER_RE.match(c.stem) and len(ORDER_RE.match(c.stem).group(1)) <= 3]
    if len(numbered) < len(clips):
        # She didn't number her clips (or the names are phone timestamps): put them in the order they were filmed
        clips, basis = clip_order.sort_files(clips)
        print(f"  Clips weren't numbered, so I put them in {'the order you filmed them' if basis == 'filmed' else 'name order'}.")
        return clips, voice
    nums = [int(ORDER_RE.match(c.stem).group(1)) for c in clips]
    dupes = sorted({n for n in nums if nums.count(n) > 1})
    if dupes:
        raise AlexError(f"Two clips share the same number {dupes}. Each clip needs its own number.", folder)
    clips.sort(key=lambda c: int(ORDER_RE.match(c.stem).group(1)))
    return clips, voice
