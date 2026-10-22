r"""Effect proofs: does each change on the "Not quite right? Change something" panel really change the finished video?

    venv\Scripts\python tests\effect_proofs.py              (about 5 to 6 minutes; NOT part of run_all.py on purpose)
    set EFFECT_PORT=8094 to move the throwaway website off its default port.

What it does, all on this laptop (no AI: ANTHROPIC_API_KEY is blanked, --no-writer, her prefs file is never read):
  1. Makes a base video from samples\recipe (voice-over) and from the first 40 s of samples\tips\Home Value.mp4
     (talking to camera), then a remake per setting the way the website's remake does it (--settings file + --reuse of
     the base job), and measures the difference in the finished files.
  2. Starts a throwaway copy of the website (temp data folder, key blank), pretends one finished video exists, sends
     POST /api/jobs/{id}/feedback {changes, remake: true} like the buttons do, waits for the remake and checks its
     settings are the old ones plus the changes, and that remember=false left the saved prefs alone.
Everything it makes goes into a temp folder that is deleted at the end. Prints SKIP and exits 0 if the samples,
ffmpeg or the Whisper model are missing.
"""
import concurrent.futures as cf
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
FAILS, ROWS = [], []
T0 = time.time()


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   ({detail})" if detail != "" else ""), flush=True)
    if not ok:
        FAILS.append(name)
    return ok


def row(setting, evidence, verdict):
    ROWS.append((setting, evidence, verdict))


def skip(why):
    print(f"SKIP effect proofs: {why}")
    sys.exit(0)


VOICE = ROOT / "samples" / "recipe"
TALK = ROOT / "samples" / "tips" / "Home Value.mp4"
if not (VOICE / "voice.m4a").exists() or not TALK.exists():
    skip("the sample clips (samples\\recipe, samples\\tips) are not on this computer")
if not shutil.which("ffmpeg") and not (ROOT / "assets" / "tools").exists():
    skip("ffmpeg is not installed")
try:
    import faster_whisper  # noqa: F401
except ImportError:
    skip("the Whisper model package is not installed")

TMP = Path(tempfile.mkdtemp(prefix="alex-effects-"))
DATA = TMP / "data"
PREFS = TMP / "prefs-none.json"   # never her real prefs: an empty file means built-in defaults
PREFS.write_text("{}", encoding="utf-8")
# Footage hold: no key may reach any step. An empty value (not a missing one) also stops .env from filling it in.
ENV = {**os.environ, "ALEX_DATA_DIR": str(DATA), "ANTHROPIC_API_KEY": "", "ALEX_PREFS_FILE": str(PREFS)}
os.environ.update({"ANTHROPIC_API_KEY": "", "ALEX_PREFS_FILE": str(PREFS), "ALEX_DATA_DIR": str(DATA)})
OUT = DATA / "out"
OUT.mkdir(parents=True)
from video_tool.config import FFMPEG  # noqa: E402  (after ALEX_DATA_DIR is set)


def pipeline(folder, job, over=None, reuse=None):
    """The command web/app.py _start + _settings_arg builds, plus --no-writer."""
    extra = []
    if over is not None:
        f = OUT / f"{job}.settings.json"
        f.write_text(json.dumps(over), encoding="utf-8")
        extra += ["--settings", str(f)]
    if reuse:
        extra += ["--reuse", str(OUT / reuse)]
    p = subprocess.run([sys.executable, "-u", "-W", "ignore", "-m", "video_tool.pipeline", str(folder), "--job-id", job,
                        "--no-writer", *extra], cwd=ROOT, env=ENV, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    log = p.stdout + p.stderr
    (TMP / f"{job}.log").write_text(log, encoding="utf-8")
    ok = p.returncode == 0 and (OUT / job / "report.json").exists()
    check(f"{job}: video made", ok, "" if ok else log[-600:])
    return log


def report(job):
    return json.loads((OUT / job / "report.json").read_text(encoding="utf-8"))


def ass(job):
    return (OUT / job / "captions.ass").read_text(encoding="utf-8")


def style(job):
    f = re.search(r"^Style: Default,[^,]*,(\d+),.*,(\d+),\d+$", ass(job), re.M)
    return int(f.group(1)), int(f.group(2))


def dialogue_text(job):
    return " ".join(re.sub(r"\{[^}]*\}", "", l.split(",", 9)[-1]).replace(r"\N", " ")
                    for l in ass(job).splitlines() if l.startswith("Dialogue:"))


def gray_frame(path, t=5.0):
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-vf", "format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, timeout=120).stdout
    return raw


def band_mean(raw, y0, y1, w=1080):
    band = raw[y0 * w:y1 * w]
    return sum(band) / max(1, len(band))


def noise_floor(path):
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "astats=measure_overall=Noise_floor:measure_perchannel=none",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    m = re.findall(r"Noise floor dB:\s*(-?[\d.]+|-inf)", p.stderr)
    return float(m[-1]) if m and m[-1] != "-inf" else None


def segs(job):
    return json.loads((OUT / job / "silence_cuts.json").read_text(encoding="utf-8"))["kept"]


try:
    talk_dir = TMP / "talk"
    talk_dir.mkdir()
    subprocess.run([FFMPEG, "-v", "error", "-i", str(TALK), "-t", "40", "-c", "copy", str(talk_dir / "01.mp4")], check=True)
    vo_dir = TMP / "recipe"
    shutil.copytree(VOICE, vo_dir)

    # ---------- base videos (Whisper runs once each; every remake reuses the transcript, like the website) ----------
    with cf.ThreadPoolExecutor(2) as ex:
        list(ex.map(lambda a: pipeline(*a), [(vo_dir, "base-vo"), (talk_dir, "base-talk")]))
    if FAILS:
        raise SystemExit(1)

    remakes = [
        (vo_dir, "vo-look", {"caption_size": 88, "caption_margin": 0.45, "caption_fixes": {"smoothie": "shake"},
                             "denoise": True}, "base-vo"),
        (vo_dir, "vo-pause", {"pause_gap": 0.45}, "base-vo"),
        (vo_dir, "vo-fillers", {"remove_fillers": False}, "base-vo"),
        (vo_dir, "vo-capoff", {"captions_on": False}, "base-vo"),
        (talk_dir, "talk-fit", {"framing": "fit"}, "base-talk"),
        (talk_dir, "talk-pause", {"pause_gap": 0.45}, "base-talk"),
        (talk_dir, "talk-hints", {"hints": "appraiser, appraisal"}, "base-talk"),
    ]
    with cf.ThreadPoolExecutor(2) as ex:
        logs = dict(zip([r[1] for r in remakes], ex.map(lambda a: pipeline(*a), remakes)))

    # ---------- captions: size, height, word fix ----------
    b_size, b_margin = style("base-vo")
    n_size, n_margin = style("vo-look")
    ok = check("caption_size 78 -> 88 changes the burned-in font size", (b_size, n_size) == (78, 88), (b_size, n_size))
    row("caption_size", f"ASS Fontsize {b_size} -> {n_size}", "works" if ok else "does nothing")
    ok = check("caption_margin 0.36 -> 0.45 raises the captions", (b_margin, n_margin) == (691, 864), (b_margin, n_margin))
    row("caption_margin", f"ASS MarginV {b_margin} -> {n_margin} px from the bottom", "works" if ok else "does nothing")
    bt, nt = dialogue_text("base-vo").lower(), dialogue_text("vo-look").lower()
    ok = check("caption fix smoothie -> shake is in the burned-in text",
               "smoothie" in bt and "smoothie" not in nt and "shake" in nt)
    row("caption_fixes", f"'smoothie' x{bt.count('smoothie')} in base, x{nt.count('smoothie')} after; 'shake' x{nt.count('shake')}",
        "works" if ok else "does nothing")
    ok = check("the word fix also reaches transcript.txt", "shake" in (OUT / "vo-look" / "transcript.txt").read_text(encoding="utf-8"))

    # ---------- captions off ----------
    nd = ass("vo-capoff").count("\nDialogue:")
    ok = check("captions_on false burns no caption lines", nd == 0 and ass("base-vo").count("\nDialogue:") > 0, nd)
    a_fr, b_fr = gray_frame(OUT / "base-vo" / "final.mp4"), gray_frame(OUT / "vo-capoff" / "final.mp4")
    y = 1920 - 691 - 120   # the band where the base video's captions sit
    diff = abs(band_mean(a_fr, y, y + 120) - band_mean(b_fr, y, y + 120))
    check("captions off: the caption band of the picture changed", diff > 2, f"mean brightness moved {diff:.1f}")
    row("captions_on", f"Dialogue lines {ass('base-vo').count(chr(10) + 'Dialogue:')} -> {nd}; caption band brightness moved {diff:.1f}",
        "works" if ok else "does nothing")

    # ---------- pauses ----------
    bl, pl = report("base-vo")["length"], report("vo-pause")["length"]
    bs, ps = segs("base-vo"), segs("vo-pause")
    overlap = any(b[0] < a[1] for a, b in zip(ps, ps[1:]))
    ok = check("pause_gap 0.7 -> 0.45 (voice-over): shorter, no piece overlaps the next (no repeated sound)",
               pl < bl and len(ps) >= len(bs) and not overlap, f"{bl}s/{len(bs)} pieces -> {pl}s/{len(ps)} pieces")
    check("progress line names the pause length she chose", "pauses over 0.45s" in logs["vo-pause"])
    row("pause_gap (voice-over)", f"{bl}s, {len(bs)} pieces -> {pl}s, {len(ps)} pieces",
        ("works but weak" if bl - pl < 0.5 else "works") if ok else "does nothing / wrong direction")
    bt_, pt_ = report("base-talk")["trimmed"], report("talk-pause")["trimmed"]
    ok = check("pause_gap 0.7 -> 0.45 (talking): the video gets shorter", pt_ < bt_, f"{bt_}s -> {pt_}s")
    row("pause_gap (talking)", f"{bt_}s -> {pt_}s", ("works but weak" if bt_ - pt_ < 0.5 else "works") if ok else "does nothing")

    # ---------- fillers ----------
    bt, ft = dialogue_text("base-vo"), dialogue_text("vo-fillers")
    bl, fl = report("base-vo")["length"], report("vo-fillers")["length"]
    ok = check("remove_fillers false keeps the 'um' in captions and sound", "um" in ft.lower().split()
               or "um," in ft.lower().split(), f"base has um: {'um,' in bt.lower().split()}")
    ok = check("remove_fillers false: the video is longer by the 'um'", fl > bl, f"{bl}s -> {fl}s") and ok
    row("remove_fillers", f"'Um,' in captions: {'Um,' in bt} -> {'Um,' in ft}; length {bl}s -> {fl}s",
        "works" if ok else "does nothing")

    # ---------- framing ----------
    a_fr, b_fr = gray_frame(OUT / "base-talk" / "final.mp4"), gray_frame(OUT / "talk-fit" / "final.mp4")
    top_a, top_b = band_mean(a_fr, 0, 200), band_mean(b_fr, 0, 200)
    bot_b, mid_b = band_mean(b_fr, 1720, 1920), band_mean(b_fr, 800, 1100)
    ok = check("framing fit: black bars top and bottom, picture in the middle", top_b < 3 and bot_b < 3 and mid_b > 15,
               f"top {top_b:.1f}, bottom {bot_b:.1f}, middle {mid_b:.1f}")
    ok = check("framing auto (base) filled the screen", top_a > 15, f"top {top_a:.1f}") and ok
    fr = [f["mode"] for f in report("talk-fit")["framing"]]
    check("report says whole frame", fr == ["whole frame"], fr)
    row("framing", f"top band brightness {top_a:.0f} (auto, face crop) -> {top_b:.0f} (fit, bars)", "works" if ok else "does nothing")

    # ---------- noise ----------
    r = report("vo-look")
    if r["denoised"]:
        nb, nn = noise_floor(OUT / "base-vo" / "final.mp4"), noise_floor(OUT / "vo-look" / "final.mp4")
        ok = check("denoise lowers the noise floor", nb is not None and nn is not None and nn < nb - 1, f"{nb} -> {nn} dB")
        row("denoise", f"noise floor {nb} -> {nn} dB", "works" if ok else "does nothing")
    else:
        check("denoise ran (or explained why not)", any(w["kind"] == "denoise" for w in r["warnings"]), r["warnings"])
        row("denoise", "noise remover not available here: " + "; ".join(w["message"] for w in r["warnings"]), "not verified")

    # ---------- hints ----------
    log = logs["talk-hints"]
    heard = (OUT / "talk-hints" / "transcript.txt").read_text(encoding="utf-8")
    ok = check("hints: a remake with new names listens again instead of reusing the old words",
               "reusing the transcript" not in log and "reusing the transcript" in logs["talk-pause"])
    fixed = "brazer" not in heard.lower()
    row("hints", f"re-transcribed: {ok}; 'brazer' heard as {'something else' if fixed else 'brazer still'}",
        ("works" if fixed else "works but weak") if ok else "does nothing")

    # ---------- music (no track is installed, so the bed itself is measured) ----------
    from video_tool import ff, music, settings
    track = TMP / "tone.wav"
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=8:c=pink:a=0.3", str(track)], check=True)
    levels = {}
    for db in (-22.0, -14.0):
        settings.configure({"music_db": db})
        bed = music.music_bed(track, 6.0, TMP, f"bed{int(-db)}.wav")
        levels[db] = ff.max_volume_db(bed).get("mean_volume")
    up = levels[-14.0] - levels[-22.0]
    ok = check("music_db -22 -> -14 makes the bed about 8 dB louder", 6.5 < up < 9.5, f"{levels}")
    row("music_db", f"bed mean {levels[-22.0]:.1f} -> {levels[-14.0]:.1f} dB (no track installed, so no music in her videos yet)",
        "works (bed only)" if ok else "does nothing")
    on = report("base-vo")["music"]
    row("music_on", f"report music = {on!r}: assets\\music is empty, so music is never added and the Music buttons are hidden",
        "nothing to change (no track)")

    # ---------- settings that cannot show an effect with these samples ----------
    rb = report("base-vo")
    row("match_clips / auto_order / match_twice", f"clip_match: {rb['clip_match'].get('reason')}: these need the AI, which "
        "this test never calls", "not verified (AI path)")
    row("skip_dead_start / stabilize", f"base: skipped_start {rb['skipped_start']}, steadied {rb['steadied']}: the samples "
        "have no shaky or dark starts to change", "not verified (nothing to change)")
    rt = report("base-talk")
    row("cut_retakes / start_at_hook", f"base found retakes {rt.get('retakes')} and no greeting {'opening' not in rt}",
        "not verified (nothing to change)")
    row("format", "changes only the titles/captions text, never the picture or sound", "not a video setting")
    row("highlights", "needs one talking video over 75 s and minutes of rendering", "not verified here")

    # ---------- the website's remake path ----------
    PORT = int(os.environ.get("EFFECT_PORT", "8094"))
    BASE = f"http://127.0.0.1:{PORT}"
    W = TMP / "site"
    (W / "out").mkdir(parents=True)
    pw = secrets.token_urlsafe(12)
    senv = {**ENV, "ALEX_DATA_DIR": str(W), "ALEX_HOSTED": "1", "ADMIN_USER": "tester", "ADMIN_PASSWORD": pw,
            "APP_PASSWORD": ""}
    senv.pop("ALEX_PREFS_FILE")
    job = "web-fx-base"
    shutil.copytree(OUT / "base-vo", W / "out" / job)
    (W / "out" / f"{job}.owner").write_text("tester", encoding="utf-8")
    (W / "out" / f"{job}.source").write_text(str(vo_dir), encoding="utf-8")
    before = {"caption_size": 90}
    (W / "out" / f"{job}.settings.json").write_text(json.dumps(before), encoding="utf-8")
    prefs = W / "users" / "tester" / "prefs.json"
    prefs.parent.mkdir(parents=True)
    saved = json.dumps({"caption_size": 66, "caption_fixes": {}}, indent=2)
    prefs.write_text(saved, encoding="utf-8")
    slog = open(TMP / "server.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "web.app:app", "--port", str(PORT)], cwd=ROOT, env=senv,
                            stdout=slog, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            try:
                if urllib.request.urlopen(f"{BASE}/healthz", timeout=2).status == 200:
                    break
            except OSError:
                time.sleep(0.5)
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        def call(method, path, body=None):
            req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                         headers={"content-type": "application/json"})
            with op.open(req, timeout=30) as r:
                return json.loads(r.read() or b"{}")
        call("POST", "/api/login", {"username": "tester", "password": pw})
        changes = {"pause_gap": 0.95, "caption_margin": 0.42, "remove_fillers": False}
        r = call("POST", f"/api/jobs/{job}/feedback", {"changes": changes, "remake": True, "remember": False})
        new = r.get("job_id")
        check("feedback with remake starts a new version", bool(new), r)
        st = {}
        for _ in range(300):
            st = call("GET", f"/api/jobs/{new}")
            if st.get("status") in ("done", "failed"):
                break
            time.sleep(1)
        check("the remake finished", st.get("status") == "done", {k: st.get(k) for k in ("status", "error")})
        from video_tool import settings as S
        want = S.clamp({**S.DEFAULTS, **before, **changes})
        got = json.loads((W / "out" / new / "report.json").read_text(encoding="utf-8"))["settings"]
        diff = {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v}
        ok = check("remake settings = the version's own settings + the button changes (her saved prefs not mixed in "
                   "except where she changed nothing)", not {k for k in diff if k != "caption_fixes"}, diff)
        check("remember=false left her saved prefs untouched", prefs.read_text(encoding="utf-8") == saved)
        log = (W / "out" / f"{new}.log").read_text(encoding="utf-8")
        check("the website remake reused the transcript (no new listening)", "reusing the transcript" in log)
        cap = (W / "out" / new / "captions.ass").read_text(encoding="utf-8")
        check("the website remake's captions use size 90 and height 0.42", ",90," in cap and f",{int(1920 * 0.42)},1" in cap)
        row("website button path", f"settings diff vs expected: {diff or 'none'}; prefs untouched", "works" if ok else "wrong")
    finally:
        proc.kill()
        proc.wait(timeout=10)
        slog.close()
finally:
    print("\nSetting | Evidence | Verdict")
    for s, e, v in ROWS:
        print(f"{s} | {e} | {v}")
    print(f"\n{len(FAILS)} failed in {time.time() - T0:.0f}s" + (": " + "; ".join(FAILS) if FAILS else ""))
    shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)
