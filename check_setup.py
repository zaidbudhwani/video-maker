"""Is this computer ready to make videos?    python check_setup.py        (add --api to test the Claude key with one tiny call)

Run it after installing on a new computer, or when something stops working. It says what is fine, what is missing
and the one line that fixes it. It changes nothing.
"""
import importlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
LEVELS = {"ok": "  OK  ", "warn": " WARN ", "bad": " FAIL "}
tally = {"ok": 0, "warn": 0, "bad": 0}


def say(level, what, fix=""):
    tally[level] += 1
    print(f"{LEVELS[level]} {what}" + (f"\n         -> {fix}" if fix else ""))


def power_values(text):
    """(plugged-in value, on-battery value) from `powercfg /query` text: the last two hex numbers are the current settings.
    Works on any Windows language. None when the output has no such numbers."""
    nums = re.findall(r"0x[0-9a-fA-F]{8}", text or "")
    return (int(nums[-2], 16), int(nums[-1], 16)) if len(nums) >= 2 else None


def powercfg(sub, setting):
    try:
        r = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", sub, setting], capture_output=True, text=True, timeout=15)
        return power_values(r.stdout) if r.returncode == 0 else None
    except Exception:
        return None


def env_key_state(env_path):
    """'nofile', 'nokey' or 'set' for the Claude key line in .env. Reads the file but never returns or prints the key."""
    path = Path(env_path)
    if not path.exists():
        return "nofile"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        name, eq, val = line.strip().partition("=")
        if eq and name.strip() == "ANTHROPIC_API_KEY":
            val = val.strip().strip("\"'")
            if val and val != "your_key_here":
                return "set"
    return "nokey"


_PLACEHOLDER = re.compile(r"your|example|xxx|___|\.\.\.|todo|tbd|paste|fill", re.I)


def contact_website_filled(text):
    """True when prompts/contact.txt has an active 'website: ...' line with a real address. A line that starts with # is a
    comment (the file ships with '# website:' switched off), and placeholders such as www.example.com don't count."""
    for line in (text or "").splitlines():
        m = re.match(r"\s*web\s*-?\s*site\s*[:=]\s*(.*)", line, re.I)
        if m and m.group(1).strip() and not _PLACEHOLDER.search(m.group(1)):
            return True
    return False

def main(api=False):
    v = sys.version_info
    say("ok" if (3, 10) <= v[:2] <= (3, 12) else "warn", f"Python {v.major}.{v.minor}.{v.micro}",
        "" if (3, 10) <= v[:2] <= (3, 12) else "Tested on 3.11. Newer versions may not have all the packages yet.")

    try:
        from video_tool.config import FFMPEG, FFPROBE, DEEP_FILTER, MUSIC_DIR, WHISPER_MODEL
    except SystemExit as e:
        say("bad", "ffmpeg isn't installed", "Install it once:  winget install Gyan.FFmpeg   (then open a new window)")
        return
    from video_tool import config
    filters = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    encoders = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    need = {"subtitles": ("captions", filters), "zscale": ("HDR phone colours", filters), "tonemap": ("HDR phone colours", filters),
            "libx264": ("the video format", encoders), "aac": ("the sound format", encoders)}
    missing = [f"{k} ({why})" for k, (why, text) in need.items() if k not in text]
    say("ok" if not missing else "bad", "ffmpeg has everything needed" if not missing else "this ffmpeg is missing: " + ", ".join(missing),
        "" if not missing else "Install the 'full' build:  winget install Gyan.FFmpeg")

    for mod, label, required in (("faster_whisper", "speech-to-text", True), ("anthropic", "the AI writer", True), ("fastapi", "the website", True),
                                 ("uvicorn", "the web server", True), ("multipart", "file uploads", True), ("dotenv", "settings file", True),
                                 ("numpy", "video checks", True), ("cv2", "video checks", True), ("mediapipe", "keeping her face in frame", False),
                                 ("scenedetect", "skipping shaky clip starts", False)):
        try:
            importlib.import_module(mod)
            say("ok", f"{label} ({mod})")
        except Exception as e:
            fix = ("pip install --no-deps -r requirements-nodeps.txt" if mod == "scenedetect" else "pip install -r requirements.lock.txt")
            say("bad" if required else "warn", f"{label} ({mod}) can't be loaded: {str(e)[:60]}", fix)

    hub = Path(os.getenv("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"
    cached = list(hub.glob(f"models--*faster-whisper-{WHISPER_MODEL}*")) if hub.exists() else []
    say("ok" if cached else "warn", f"speech-to-text model '{WHISPER_MODEL}' " + ("is downloaded" if cached else "isn't downloaded yet"),
        "" if cached else "The first video will download about 500 MB and look stuck for a few minutes. Make one test video first.")
    face = ROOT / "assets" / "models" / "blaze_face_short_range.tflite"
    say("ok" if face.exists() else "warn", "face-detection model " + ("is there" if face.exists() else "is missing: talking videos get black bars instead of a face-centred crop"),
        "" if face.exists() else "Download https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite to assets\\models\\")
    say("ok" if DEEP_FILTER else "warn", "noise remover " + ("is installed" if DEEP_FILTER else "isn't installed (the 'Remove background noise' option will do nothing)"),
        "" if DEEP_FILTER else "Optional. See README: 'Noise removal'.")
    if os.name == "nt":
        say("ok" if Path(r"C:\Windows\Fonts\arial.ttf").exists() else "warn", "caption font (Arial)")
    else:
        found = shutil.which("fc-match") and "arial" in subprocess.run(["fc-match", "Arial"], capture_output=True, text=True).stdout.lower() + "liberation"
        say("ok" if found else "warn", "caption font", "" if found else "apt install fonts-liberation")

    from dotenv import dotenv_values
    env = {**dotenv_values(ROOT / ".env"), **{k: v for k, v in os.environ.items() if k.startswith("ANTHROPIC")}}
    state = env_key_state(ROOT / ".env")
    say("ok" if state == "set" else "warn", {"set": ".env has a Claude key line (the key itself isn't shown)", "nokey": ".env has no Claude key line filled in",
                                              "nofile": "there is no .env file"}[state],
        "" if state == "set" else "Open .env in Notepad and add:  ANTHROPIC_API_KEY=your key. Never paste a key into a chat or an email.")
    key = (env.get("ANTHROPIC_API_KEY") or "").strip()
    if not key or key == "your_key_here":
        say("warn", "no Claude key: titles are simpler (from her own words) and clips aren't matched to her words",
            "Put ANTHROPIC_API_KEY=... in the .env file. Never paste a key into a chat or an email.")
    elif api:
        try:
            from video_tool import ai
            ai.client(timeout=30).messages.create(model=config.CLAUDE_MODEL, max_tokens=8, messages=[{"role": "user", "content": "Say ok"}])
            say("ok", f"Claude key works (model {config.CLAUDE_MODEL})")
        except Exception as e:
            say("bad", f"Claude key is set but the test call failed: {str(e)[:110]}",
                "Check the key at console.anthropic.com, that it has credit, and (if it says 'workspace') add ANTHROPIC_WORKSPACE_ID to .env.")
    else:
        say("ok", "Claude key is set (run with --api to test it)")

    try:
        from web import auth
        n = len(auth.load_users())
        say("ok" if n else "warn", f"{n} website account(s)", "" if n else "Add one:  venv\\Scripts\\python manage_users.py add NAME --admin")
    except Exception as e:
        say("bad", f"accounts file can't be read: {str(e)[:70]}", "users.json may be damaged: restore it from a backup (maintain.py backup)")
    ex = (ROOT / "prompts" / "examples.md").read_text(encoding="utf-8") if (ROOT / "prompts" / "examples.md").exists() else ""
    say("ok" if ex and "(paste her real" not in ex else "warn", "her writing samples for the AI are filled in" if ex and "(paste her real" not in ex else "prompts/examples.md still has empty sections")
    tracks = [p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in config.AUDIO_EXT] if MUSIC_DIR.exists() else []
    say("ok" if tracks else "warn", f"{len(tracks)} music track(s)" if tracks else "no music track (videos are made without music)",
        "" if tracks else "Optional. Put a royalty-free track in assets\\music\\ (YouTube Audio Library).")
    if os.name == "nt":
        lid = powercfg("SUB_BUTTONS", "LIDACTION")
        names = {0: "do nothing", 1: "sleep", 2: "hibernate", 3: "shut down"}
        if lid is None:
            say("ok", "no lid setting found (a desktop computer, or Windows didn't say): nothing to check")
        elif lid[0] == 0:
            say("ok", "closing the lid while plugged in does nothing, so the website keeps running")
        else:
            say("warn", f"closing the lid while plugged in makes this computer {names.get(lid[0], 'stop')}: the website stops and Alex's link stops working",
                "Keep the lid open, or: Control Panel > Power Options > 'Choose what closing the lid does' > plugged in > Do nothing.")
        idle = powercfg("SUB_SLEEP", "STANDBYIDLE")
        if idle is not None and idle[0] != 0:
            say("ok", f"Windows would sleep after {idle[0] // 60} minutes idle, but the website asks it to stay awake while it runs")
    help_line = (dotenv_values(ROOT / ".env").get("HELP_CONTACT") or "").strip()
    say("ok" if help_line else "warn", "the 'need help?' line Alex sees under errors is set" if help_line else "HELP_CONTACT isn't set: errors tell Alex nothing about who to contact",
        "" if help_line else "Add a line to .env, for example:  HELP_CONTACT=Text the maintainer at 319-555-0100")
    contact = ROOT / "prompts" / "contact.txt"
    if not contact.exists():
        say("warn", "prompts/contact.txt is missing: her website isn't added to the captions", "Create prompts/contact.txt with a line such as:  website: www.her-address.com")
    elif contact_website_filled(contact.read_text(encoding="utf-8", errors="replace")):
        say("ok", "prompts/contact.txt has her website")
    else:
        say("warn", "prompts/contact.txt has no website filled in yet", "In prompts/contact.txt, remove the # in front of the website line and put her real address after it.")
    free = shutil.disk_usage(config.DATA_DIR).free / 1024 ** 3
    say("ok" if free > 20 else "warn" if free > 5 else "bad", f"{free:.0f} GB of disk space free", "" if free > 20 else "Run:  venv\\Scripts\\python maintain.py slim --yes")

    print(f"\n{tally['ok']} fine, {tally['warn']} to look at, {tally['bad']} to fix.")
    return tally["bad"]


if __name__ == "__main__":
    sys.exit(1 if main("--api" in sys.argv) else 0)
