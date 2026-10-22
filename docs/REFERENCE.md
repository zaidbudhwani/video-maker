# Video Maker: reference

> **The long reference.** Settings, `report.json` / `copy.json` fields, website endpoints, long video mode and the history of
> decisions. It grew during the build, so its first sections ("Before you start", "Setup") are older than the installers:
> to install, use `../README.md` or `INSTALL.md`. To understand the project, start with `ARCHITECTURE.md`.

Turns a broker's raw phone clips and voice-overs into finished vertical short videos.

Feature: **Long video mode** (Shorts from one long YouTube-style video, plus a YouTube post for the long video itself): YouTube first
(long video and Shorts), the same vertical clips reused on TikTok, Reels and Facebook with a different caption for each. See "Long video mode" below.

---

## Before you start Claude Code

Three things must be in place or the tool produces garbage.

**1. Her clips**
Download her Google Drive folders. Put one test set in `samples/`. Clips can have any names: un-numbered clips play in the order they were filmed (see Clip order below). Clips named `01.mp4`, `02.mp4`, `03.mp4` play in that numbered order. Put the voice-over file in the same folder as `voice.m4a` or `voice.mp3`.

**2. Her voice examples**
Fill in `prompts/examples.md` with 10 to 15 real things she wrote. Captions, video titles, texts, emails. Do not write these yourself. The whole point is matching her, not us.

**3. Her disclosure line**
Put her brokerage disclosure in `prompts/disclosure.txt`. Ask her for the exact wording Example requires.

Without these three, stop. Do not start building.

---

## Setup

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install --no-deps -r requirements-nodeps.txt   # scenedetect: its opencv-python would break mediapipe's cv2
```

You also need ffmpeg installed on your machine.

Mac: `brew install ffmpeg`
Windows: download from ffmpeg.org and add it to PATH
Linux: `sudo apt install ffmpeg`

Check it works:
```bash
ffmpeg -version
```

---

## API key

```bash
cp .env.example .env
```

Put the Anthropic API key in `.env`. Never commit this file.

---

## Starting Claude Code

From the project folder:

```bash
claude
```


It will send back a plan. Read it. Push back on anything that does not match the brief. Only say continue when you agree.

---

## Rules while building

Build one piece at a time. After each piece, run it on the real files in `samples/` and look at the output yourself. Do not let it build three things at once.

Order: transcribe, assemble, trim silence, captions, music, writer, pipeline. Web page last.

If something breaks, give Claude Code the actual error text, not a description of it.

Commit after every working piece. Never commit videos or `.env`.

---

## Folder layout

```
video-maker/
  prompts/
    examples.md         her real writing, for voice matching
    disclosure.txt      brokerage disclosure line
  samples/              her test clips and voice file
  assets/music/         royalty-free tracks only
  out/                  generated files, one folder per job
  .env                  API key, never committed
```

---

## Music warning

Only royalty-free tracks in `assets/music/`. Copyrighted music gets YouTube videos muted or struck, and this is her real channel. Use the YouTube Audio Library. Ask her what she uses now before adding anything.

---

## Who owns what

Prompts and AI output quality; the video pipeline (ffmpeg, joining clips, audio sync, captions); transcription, the interface, the calendar and metrics.

Everyone watches every video before it is posted.

---

## What we measure

Time her editing one video by hand before the tool exists. Write the number down. That is the comparison for the final presentation.

Then track: hours per video before and after, videos posted per week, titles and captions she used unchanged, and her direct quotes.

---

## Running the tool

Windows setup (already done on the maintainer's machine):

```
winget install Gyan.FFmpeg
winget install Python.Python.3.11
py -3.11 -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\pip install --no-deps -r requirements-nodeps.txt
```

Make a video (folder = `01.mp4`, `02.mp4`... + one voice file):

```
venv\Scripts\python -m video_tool.pipeline samples\tour
```

Result: `out/<job_id>/final.mp4`, `copy.json` (titles, hook, description, CTA), `report.json`,
and every intermediate file (`words.json`, `voice_trimmed.wav`, `normalized/`, `captions.ass`...).

Flags: `--no-music`, `--no-writer`. Music is used automatically if a track is in `assets/music/`.

Code lives in `video_tool/`: transcribe, silence, assemble, captions, music, writer, pipeline.

Voice-over mode skips dead footage at the start of a clip (camera still swinging up from the
floor, black frames) using camera-motion and brightness checks plus PySceneDetect (`deadstart.py`).
It is conservative: at most 3s or a quarter of a clip, nothing when unsure. Each skip is printed, and
recorded as `skipped_start` in `timeline.json` and `report.json`. Without PySceneDetect installed
(`requirements-nodeps.txt`) videos are still made, the clip starts just aren't checked. Turn it off with the
"Keep the start of each clip" button (setting `skip_dead_start`). Check one clip:
`venv\Scripts\python -m video_tool.deadstart samples\03.mp4`.

## Upload page

Double-click **Start Video Maker.bat**. A browser opens at http://localhost:8000.
Drop in the clips + voice-over, fix the order with the arrows, click **Make my video**.
Leave the black window open while it works. Close it when done.

## Titles without an API key

No key in `.env`? Every video still gets `copy.json`: titles, hook, description and CTA pulled
from her own sentences, plus the disclosure and Fair Housing / hype warnings. It also saves
`ai_prompt.txt`: paste it into the free claude.ai chat for polished AI options (the page has a
"Copy prompt" button). Add a key later and the tool writes the AI version automatically.

## Sharing it as a website (the broker uses it from anywhere)

The tool keeps running on this computer. Cloudflare gives it a public link, so her uploads
and all the processing stay here. Free, no account needed.

1. Open `.env` in Notepad and set `APP_PASSWORD=` to a password you choose. Save.
2. Double-click **Start Website.bat**.
3. Find the line ending in `.trycloudflare.com`. That's the link. Send it to the broker with the password.
4. Keep the window open and the computer awake and online while she uses it.

The link changes every time you restart. For a permanent address you need a free Cloudflare
account plus a domain (a "named tunnel").

## Talking-to-camera videos (no voice-over)

Upload clips without an audio file and the tool uses the clips' own sound: it transcribes
what she says, cuts the pauses out of picture and sound together, and adds captions.
Square or landscape clips are shown whole with black above and below so her face isn't cropped.
Anything over 60 seconds is split into Part 1, Part 2... at natural pauses (for a long video she wants cut into Shorts instead, see "Long video mode"). Every file is
vertical 1080x1920 and under 60s, so the same file posts to TikTok, Instagram Reels and YouTube Shorts.

Also in talking mode: if she says a line twice in a row the earlier take is cut and the last one kept (`cut_retakes`; the page lists each cut so she can ask for both takes back). A greeting before the real first line ("Hi everybody, it's the broker") is noticed and offered as **Start at the hook** but only cut when she asks (`start_at_hook`). If a "talking" upload has almost no speech (she forgot the voice-over), the page says so in plain words instead of making an empty video.

## Long video mode: Shorts from a long video (YouTube first)

**Why it exists.** the broker's email of 2 Oct 2026: YouTube is her home base, with longer videos where people get the full picture of her and her
life in Iowa, and she has old content that could be revived. From those she wants short vertical clips for TikTok, Instagram Reels and
Facebook Reels, with a different caption for each platform. Facebook also gets her CRM auto-posts for listings and market updates, and she
wants those kept separate from this tool, so **the CRM posts are out of scope and stay out**. Hence "YouTube first (long video and Shorts),
with the same vertical clips reusable on the other platforms".

**What she does.** On the upload page she picks **Shorts from a long video** (instead of **A video from my clips**), adds ONE finished long
video (more than 75 seconds; she is told how long it will take), chooses **How many Shorts?** (1 to 5, the box starts at 3) and taps
**Make my Shorts**. No voice-over is used. Her long video is not re-rendered or changed: she uploads her own file to YouTube herself.

**What she gets.**
1. Up to N vertical Shorts (1080x1920, each 25 to 58 seconds, `final_part1.mp4`...), cut from the best self-contained moments of what she
   says: whole sentences only, never overlapping, at least 5 seconds apart. Each Short has plain captions burned in, a face-centred crop
   (or the whole frame with bars, as in talking mode), three titles and a caption for YouTube Shorts, TikTok, Instagram Reels and
   Facebook Reels. Each Short is made by the ordinary talking-video pipeline (pauses and fillers cut, retakes kept as the last take).
   The page labels them "Short 1", "Short 2"... and says where in the long video each came from.
2. **A YouTube post for the long video itself:** three title ideas (70 characters or fewer), a description with chapter lines
   (`0:00 Intro`...) and her closing line, tags, and a captions file for the whole video (`long.srt`). Chapters follow YouTube's
   rules (first at 0:00, at least three, each at least 10 seconds); if a list can't follow them it is left out rather than sent broken.
3. All of it comes in the **Post pack** zip too: the Shorts, a captions file and cover and text for each, `youtube-long-video.txt`
   (titles, description, tags) and `long.srt`.
Nothing posts anywhere. She uploads the long video, the captions file and the Shorts herself, and in each Short's details picks the long
video under "Related video".

**How long it takes.** The website's estimate (`web/app.py`, measured on this laptop) is: seconds = 0.22 x the video's length + 60 per Short
+ 20. That is about 3 minutes for the 150-second Home Value sample with 2 Shorts, about 6 minutes for a 10-minute video with 3 Shorts and
about 10 minutes for a 30-minute video with 3 Shorts (12 with 5). Only short test files were timed end to end; the longer
figures come from the formula, not from separate timings. The page shows which stage it
is in, how much has been listened to and about how many minutes are left, and keeps working if she leaves the page. A render is stopped
after 2 hours (`RENDER_TIMEOUT_LONG`); the site refuses a video over 4 hours.

**Command line.** `venv\Scripts\python -m video_tool.pipeline <folder with ONE long video> --highlights 3 --job-id mylong-1` (add
`--no-writer` for no AI at all). With a voice-over, more than one clip, or a video of 75 seconds or less it makes the ordinary video
instead and says so in `warnings`. The website setting is `highlights` (below).

**How it works** (`video_tool/highlights.py`, `video_tool/longform.py`, `pipeline.long_video`; progress lines `1/6` to `6/6`):
1. the whole long video is transcribed once, locally (faster-whisper), nothing is sent anywhere;
2. `highlights.pick_moments` chooses the moments. With a Claude key, Claude reads the numbered sentences and proposes moments; each pick is
   checked in code (whole sentences, 25 to 58 s, not overlapping, hook inside the stretch). Without a key, or when Claude fails or
   returns too few, a plain scoring of the sentences fills in (`source: "fallback"` in the pick; a pick from Claude is `"ai"`);
3. each moment is cut to its own small file and sent through the normal talking-video pipeline as a child job (`<job>-h1`...), reusing the
   words already heard. The child folders are removed afterwards; a Short that fails is left out with a warning and the others are kept;
4. `longform.build_kit` writes the YouTube post from the transcript. Claude's answer is checked (no hype words, no home status such as for
   sale or sold, no link, email or @name she does not have, no invented place or number, Fair Housing words for property talk, titles
   within 70 characters) and retried once; if it still breaks a rule, or there is no key, the plain kit is built from her own sentences
   (`source: "fallback"`). `long.srt` is written the same way the burned-in captions are made.

**Limits.** One long video at a time per upload (the render lock means a second job waits its turn). 1 to 5 Shorts. Fewer Shorts than asked
when the video has fewer good moments (a plain note says so); if nothing stands out, one Short is made from the best stretch found. A video
with no speech stops with a plain message. Only transcript text goes to Claude in this mode: never a picture, a video piece, a sound
clip or a file name (each child job is named "Short 1", "Short 2"...), and `match_clips` does not apply to talking mode. Every AI step has
a no-AI fallback and a warning never stops a Short.

**Outside AI.** Only Claude is wired in, and only when `ANTHROPIC_API_KEY` is set in `.env`. With the key blank every step uses its plain, no-AI fallback: the picks and the YouTube post are built from the transcript alone. Nothing else is sent anywhere.

### Settings key, files and routes for long video mode

Setting `highlights` (`video_tool/settings.py`, `clamp_highlights`): `0` = off (the default), `1` to `5` = how many Shorts to cut from ONE
long talking video. Anything that is not a plain whole number (text, `null`, `true`) means off. Per-video only: it is not remembered in
`prefs.json`. On the website it is the `highlights` field of `POST /api/uploads` or `POST /api/jobs` (1 to 5, else a plain 400); on a
finished Shorts video "Not quite right?" offers **How many Shorts?** and Remake uses the saved transcript.

`report.json` (`mode: "highlights"`) adds, to the usual fields: `parts` (`[{file, length, size_mb, text, start_at, end_at, hook, why}]`,
`start_at` and `end_at` in seconds of the long video), `highlights` (`[{start_at, end_at, hook, why, source}]`, `source` `ai` or
`fallback`), `original` (the long video's length in seconds), `longform` (`{file: "longform.json", srt: "long.srt"}`), `framing`
and `retakes` (with the Short each belongs to), and `warnings` of kind `highlights` (fewer Shorts than asked, no standout moment, a
moment that could not be made).

`copy.json` is the ordinary shape, with the first Short's copy on top and `parts` = one entry per Short:
`[{label: "Short 1", part, of, file, titles, hook, description, cta, captions}]`. `ai_cost_usd` adds the Short copy and the picks and the
YouTube post. Each Short's `hook` (and the start of its captions) is the opening line the picker chose for that moment.

`longform.json`: `{titles: [3 strings], description, chapters: [{at, label}], tags, source: "ai" | "fallback", ai_cost_usd}`; `at` is
seconds from the start of the long video; the description already holds the chapter lines and her closing line (and her disclosure line
only if `prompts/disclosure.txt` is filled in). `long.srt` is the whole video's captions.

Other files: `transcript.txt` and `words.json` (the whole long video), `final_part1.mp4`... and `captions_part1.ass`... per Short.

Routes (owner or admin; plain 404 in her words when the video has no long-video post):

| Method and path | What it does |
|---|---|
| GET `/api/jobs/{job}/longform` | The contents of `longform.json`. |
| GET `/api/jobs/{job}/long.srt` | The captions file, downloaded as `<first title>-captions.srt`. |
| GET `/api/jobs/{job}/postpack` | The zip; for a Shorts video it also holds `youtube-long-video.txt` and `long.srt`. |

`GET /api/jobs/{job}` also returns a `long` block while a Shorts video is made (stage, how much was heard, which Short, `progress`,
`minutes_left`, `estimate_minutes`), and `GET /api/videos` rows carry `shorts`.

### Tests for long video mode

Unit tests (plain scripts, no Claude call, no video made; AI paths use fake clients): `venv\Scripts\python tests\test_highlights.py`
(picking, cutting, the job with a fake pipeline, settings), `tests\test_longform.py` (chapters, the plain kit, the Claude path with a
fake client, the captions file, the post pack), `tests\test_longvideo_web.py` (the upload checks and 400s, the estimate, the progress
lines, a finished Shorts job through the status route, a restart in the middle). Last run 10 Oct 2026: all three exit 0, no FAIL lines.
Browser part: `set E2E_PORT=8181` and `set E2E_ONLY=longvideo` then `venv\Scripts\python tests\e2e_site.py` runs
`tests/e2e_part_longvideo.py` in a phone-sized Chromium: the upload choice and its refusals, the result page of a finished Shorts job,
and one real run of the 150-second Home Value sample through the site, 2 Shorts, with the key blank. Real engine runs on the same
sample were also made from the command line with the key blank.

**What is NOT tested, said plainly:**
- **Claude's picks and the Claude YouTube post have not been run on her real videos.** Her footage and words may not go to an AI service
  until she approves, so the real-file runs used the plain picks and plain kit. The Claude path was tested with fake clients and with
  synthetic text that is not hers.
- Her actual old YouTube videos at all: the real quality of the picks and chapters on them is unknown.
- A real phone uploading a long video (the upload is chunked and resumes, but a 30-minute phone video through the tunnel was not tried).
- Videos over about 30 minutes (the site accepts up to 4 hours; nothing past a 7-minute test file was made).
- Posting: nothing posts, so nothing about YouTube Studio itself was tested.



Tick "Remove background noise (wind, fans, traffic)" before **Make my video**, or use the
"Remove background noise" button under a finished video (setting `denoise`). It works for
voice-overs and for talking-to-camera clips. Her voice is kept natural: the background is turned
down by at most 24 dB, never gated to dead silence. Timing is untouched, so captions stay lined up.

It uses DeepFilterNet's standalone program, which is not in git (27 MB). Download it once:

https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-x86_64-pc-windows-msvc.exe

and put it in `assets/tools/` (any name starting with `deep-filter` works; on Windows it must end in `.exe`). Mac and Linux
builds are on the same release page (https://github.com/Rikorose/DeepFilterNet/releases/tag/v0.5.6);
on those run `chmod +x` on the file. Do NOT `pip install deepfilternet`: it downgrades numpy and
breaks Whisper and MediaPipe. If the program is missing the video is still made with its original
sound and a one-line heads-up. Files kept in `out/<job_id>/`: `voice_clean.wav` (voice-over mode) or
`clip_sound.wav` + `clip_sound_clean.wav` (talking mode); `report.json` has `"denoised": true/false`.

## Feedback

Under every finished video there's a "Not quite right?" panel: caption size and height,
captions off, word fixes (e.g. "Alexe" -> "the broker"), cut more / fewer pauses, fill vs whole frame,
music volume, and a comments box. "Remake video" re-renders with the changes (reuses the
transcript, so it's quicker). "Use these settings for all my future videos" saves them to
`prefs.json`. Every piece of feedback is logged to `out/feedback.jsonl` for the team.

## Accounts, typed feedback, always-on hosting

**Accounts.** On the shared link everyone signs in with their own username and password and gets their own
videos and saved caption/pause/music choices. Add people: `venv\Scripts\python manage_users.py add alex`
(add `--admin` for yourself), or from the **Admin** page. The old `APP_PASSWORD` becomes the login for user `alex`.
Running on your own computer with Start Video Maker.bat also asks for the sign-in once any account exists.

**Typed feedback is applied.** Whatever someone types in "Anything else?" is read by Claude, turned into the matching
settings (caption size/position, pauses, music, framing, word fixes...) and the video is remade. Anything no setting can
do ("put clip 2 first") is kept as an open item on `/admin` (Copy open requests -> paste into a Claude session to have
the code changed). Without an API key a basic keyword reader handles the common asks.

**Always-on.** The `Dockerfile` packages the site for any small cloud server (about 4 GB RAM, 2 CPUs, a disk
mounted at `/data`). Set environment variables `ADMIN_USER`, `ADMIN_PASSWORD`, `ANTHROPIC_API_KEY` (and
`ANTHROPIC_WORKSPACE_ID` if needed). The host gives a permanent https address; a domain is optional.

## Clip matching (Claude vision)

Voice-over mode, on by default (`match_clips`). Claude looks at a few still frames from the start of
each clip and at the phrases of her voice-over, and the tool times each cut so the footage matches what
she is saying. About 8 cents and a minute for an 11-clip tour; remakes reuse the scores for free. It
only replaces the even split when clearly better, and with no API key or any error the even split is used.

When a clip plainly belongs at another moment (`auto_order`, on by default) it is moved there: the tool
tries each strongly matched clip in every position, one clip at a time, and keeps a move only if the
whole plan grades at least 5% better. Anything it doesn't move but still looks out of place gets a
plain "heads up" on the page. Claude's scores wobble a point or two from run to run; judging moves by
the whole plan instead of one frame's score is what keeps the result the same between runs.

`match_twice` (off by default; "Double-check the clip matching" on the website) has Claude score the
clips twice at the same time and averages the two. It doubles the matching cost (about 8 cents more on
a long tour) and is only worth it if a video keeps coming out differently between remakes.
NOTE: this sends still frames of her footage to Anthropic (the original brief said transcript only). **Since 9 Oct 2026 nothing of hers may go to an outside AI until the broker herself approves**; runs on her real files use a blank key (see "Long video mode").
the maintainer decided on 2 Oct 2026 to have it on by default. Filenames are never sent.

**On-laptop matcher (experiment, off by default).** Setting `match_engine: "clip"` (not on any page, not remembered,
never the default) scores the same still frames with CLIP ViT-B/32 running on this computer (`video_tool/localmatch.py`,
model files in `assets/models/clip/`, onnxruntime on the CPU). Nothing is sent anywhere and no key is needed; it takes
a few seconds. Each frame is cut into three 224x224 squares (top, middle, bottom) and compared with the phrase; the best
square counts. Similarities are centred per phrase and per frame, and a score is 5 plus that / 0.01, kept between 0
and 10 (fixed rules, nothing learned; picked on half of her tour's phrases). Embeddings are cached in
`match/clip_embeddings.npz`; `match_twice` is ignored (same answer every time). Missing model files give her order with
an even split and a plain reason. On her house tour (`tests/evaluate_local_matcher.py`, offline) the finished plan had
the right clip on screen 78% of the supported narration (the 58% of the narration some clip can show; Claude 82%, her own
order 24%; 69% on the held-out half of the phrases, a split inside one tour, not new houses), but
its top phrase per clip was right for only 2 of 11 clips (Claude 8), and nearby settings scored anywhere from 24% to
79% (median 45%), so it is an option, not a replacement for Claude. Tests: `tests/test_localmatch.py`.

Disclosure: optional since the broker's 2 Oct 2026 email ("mostly on print material"). Fill in
`prompts/disclosure.txt` to have a line added to every description.

## Errors and help

Error messages on the website are plain English: no folder paths, and files are named the way
she uploaded them ("Clip 2 (Kitchen.mov)"). The technical detail goes to `out/failures.jsonl`
for the team. After a failed upload her clips, their order and the voice-over stay in the list.
Set `HELP_CONTACT=` in `.env` (for example `Text the maintainer at ...`) to show "Need help? ..." under errors.
"Use these settings for all my future videos" starts unticked, so a one-off tweak stays one-off.

## Keeping it healthy (for the team)

- **Disk space.** Every video keeps about 330 MB, 80% of it working files nobody needs afterwards.
  `venv\Scripts\python maintain.py usage` shows where the space went. `maintain.py slim --yes` drops the
  working files of videos older than 7 days (finished videos, titles and remake data stay). `maintain.py clean --days 90 --yes`
  deletes whole old videos and their uploads. Without `--yes` they only show what they would do.
- **Backups.** `venv\Scripts\python maintain.py backup` zips the accounts, saved settings, titles and history
  to `Documents\video-backups` (newest 8 kept). It leaves out `.env` and the session key; add `--with-videos` for the finished videos.
- **Same versions everywhere.** `requirements.lock.txt` is exactly what is installed and tested now. To rebuild on another computer:
  `pip install -r requirements.lock.txt` then `pip install --no-deps -r requirements-nodeps.txt`.
- **Uploads** go up in small pieces (8 MB), so they stay under Cloudflare's 100 MB request limit, survive a dropped connection and
  resume where they stopped. The server also checks file types and the voice-over length before any video work starts.
  iPhone Voice Memos (.m4a, .qta), WhatsApp/Android voice notes (.opus, .ogg, .amr) and .3gp/.webm/.mkv/.avi clips are accepted.
- **Sleep.** While the site is running, the computer is asked not to go to sleep (closing the lid still does: keep the lid open, or set Windows' lid action to *Do nothing*; `check_setup.py` warns about it).
- **Restarts and logs.** `Start Website.bat` starts the website and the tunnel again by itself after a crash or a Wi-Fi drop (the tunnel then gets a new link, shown in the window and in `current-link.txt`), writes the website's messages to `out\server.log`, and stops leftover video jobs when it starts. `Schedule Backup.bat` makes a weekly backup.
- **If the site restarts during a video,** the broker sees "The Video Maker was restarted" with a Make it again button that reuses her saved files.

## What the website does now (a tour of the screens)

- **Upload page.** Pick clips (the order is hers to change with the arrows), add a voice-over or **record one in the browser**
  (needs microphone permission and a secure address: the https link or this computer), and optionally fill in: *Names or places I should know* (setting `hints`), *What kind of video is
  this?* (setting `format`), *Remove background noise*, *Double-check the clip matching* (`match_twice`) and *Don't share any pictures from my clips* (turns clip matching off for this video; recorded as `privacy: "private"`).
  *What should I film this week?* gives
  ideas (Claude when there is a key, a built-in seasonal list otherwise). *See an example of a finished video* plays the demo
  video (see `/api/demo`). Uploads go up in 8 MB pieces and resume.
- **Result page.** The video(s), then: next steps, a tab per platform for the caption (YouTube Shorts, TikTok, Instagram Reels,
  Facebook Reels) with Copy buttons, three title ideas, a "Before you post" checklist, **Which clip is where**, **What the captions
  say**, plain "What I did" chips (cut pauses, cut repeated lines, steadied a clip, moved a clip...), "Check these words"
  (uncertain words), "Said but not shown" (things she talked about that no clip shows, with a filming idea), a greeting
  that can be cut ("Start at the hook"), a **Post pack** download, a posting row (Posted it? per platform, with a reminder file),
  and the **Not quite right?** change panel (captions, pauses, framing, music, word fixes, clip order, a typed request).
- **My videos.** List with pictures, *Delete* (one video or every version), posting log (Draft / Reviewed / Posted), a "this week"
  card against her goal of 3 to 5 posts, a reminder (`.ics` calendar file, nothing is sent), and an **Account** panel to change
  her password.
- **Sign-in and Team page.** Sign-in has the hold-to-peek eye on every password box. The Team page (admins) shows a health card
  (disk space, whether the Claude key is present, last video, whether the computer is kept awake), the last failed videos with
  the technical detail, open requests, and people.
- **Phone app feel.** `manifest.webmanifest`, `sw.js` and two icons let her "Add to Home Screen".

## Settings reference (`video_tool/settings.py`)

Order of precedence: built-in defaults, then her remembered `prefs.json` (look-and-feel keys only), then the overrides for one run
(`--settings file.json`, or the website's Remake). Values are clamped by `LIMITS`.

| Key | Default | Meaning |
|---|---|---|
| `captions_on` | true | Burn captions in. |
| `caption_size` | 78 (54 to 110) | Font size on the 1080x1920 frame. |
| `caption_margin` | 0.36 (0.33 to 0.60) | Distance of captions from the bottom as a share of the height. Never inside the bottom 20%. |
| `caption_fixes` | `{}` | Whole-word replacements (`{"Alexe": "the broker"}`). |
| `pause_gap` | 0.7 (0.4 to 1.5) | Pauses longer than this many seconds are cut. |
| `remove_fillers` | true | Cut "um" and "uh". |
| `skip_dead_start` | true | Voice-over mode: skip a shaky or black start of a clip. |
| `match_clips` | true | Voice-over mode: Claude looks at still frames and times each clip to her words. |
| `auto_order` | true | Move a clip to where she talks about it when the whole plan grades at least 5% better. |
| `match_twice` | false | Score the clips twice and average. Doubles the matching cost. Only a real `true` turns it on. |
| `hints` | "" | Per video: names, streets and numbers to listen for. Max 400 characters. They bias the speech-to-text spelling as hotwords and are never repeated back. Not remembered. |
| `format` | "auto" | `auto`, `house_tour`, `recipe`, `tips`, `market_update`, `qa`. Shapes titles, captions, closing line, hashtags and the "film this" list (`video_tool/formats.py`). Not remembered. |
| `cut_retakes` | true | Talking mode: when she says a line twice in a row, keep the last take. Each cut is listed in `report.json` `retakes`. |
| `start_at_hook` | false | Talking mode: drop a greeting before the real first line when asked to. The greeting is always detected and reported as `opening`; it is only cut when this is true. |
| `stabilize` | true | Shaky clip: use a steadier part of the same clip. |
| `framing` | "auto" | `auto` (face-centred crop, else bars), `fill`, `fit`. |
| `highlights` | 0 (0 to 5) | Long video mode: 0 = off; 1 to 5 = cut that many Shorts from ONE long talking video (over 75 seconds) and write the YouTube post for it. Per video, never remembered. See "Long video mode". |
| `denoise` | false | Remove background noise (needs `assets/tools/deep-filter*.exe`). |
| `music_on`, `music_db` | true, -22 (-32 to -14) | Music bed and its level. |
| `music_duck` | false | Opt-in gentle ducking: the music dips about 4 dB more while she speaks (sidechain from the levelled voice, 150 ms attack, 900 ms release, before the peak guard). Measure with `tests/measure_ducking.py`. Not yet judged by ear: keep off until a blind listening comparison. |

"Use these settings for all my future videos" saves only the look-and-feel keys (`REMEMBER_KEYS`: captions, size, height, pauses,
fillers, dead start, stabilize, framing, denoise, music). Per-video keys (`hints`, `format`, `cut_retakes`, `start_at_hook`,
`match_twice`, `match_clips`, `auto_order`, `highlights`) are never saved, so one video's choices cannot leak into the next. The page's
`privacy` override is not in `DEFAULTS`: the server adds it per video next to `match_clips: false`.

## Files a finished video leaves (`out/<job>/`)

### `report.json`

`job_id`, `mode` (`voice` or `own_audio`), `parts` (`[{file, length, size_mb, text}]`), `length`, `music`, `denoised`,
`copy` (`ok`, `ok (auto)` or `skipped`), `seconds_to_build`, `settings`, `warnings` (`[{clip, kind, message}]`; kinds include
`wide`, `denoise`, `clipping` and the shaky, blurry and dark clip checks), `skipped_clips` (names as she gave them: footage that was
not needed) and `uncertain_words` (`[{word, at, p, part}]`, least sure first, at most 12).
Voice-over mode adds `not_shown` (`[{at, said, idea}]`, at most 3: said but not shown, worked out from the clip-matching scores
already saved, no extra AI call). Talking mode adds `original`, `trimmed`, `framing`, `retakes` (`[{at, said, removed_at}]`) and
`opening` (`{greeting_seconds, said, hook_at, applied}`). Clip fitting adds `clips_total`, `voice`, `cut_from_end` (seconds of
unused footage), `short_by` (seconds the last frame is held), `timeline`, `clip_match`, `match_warnings`, `steadied`, `moved`,
`skipped_start`. `ai_error` (`{kind, message}`) appears when a Claude step failed (the video was still made). `final` (a disk path)
is removed before the page sees the report.

### `copy.json`

`source`, `topic` (`property_or_location` or `general`), `titles` (three), `hook`, `description`, `cta`, `captions`
(`youtube_shorts`, `tiktok`, `instagram_reels`, `facebook_reels`; each within its length limit, with the disclosure once at the
very end when `prompts/disclosure.txt` is filled in), `review_notes` (things to check: phone number, website, Fair Housing words,
uncertain words), `format` and `format_label`, `checklist` ("Before you post" lines), `ai_cost_usd`, and for a split video `parts`
(`[{label, part, of, file, titles, hook, description, cta, captions}]`). With no AI step `ai_prompt.txt` is saved beside it.

### Post pack (zip)

Video file(s), `captions.srt` (same lines as burned in), `cover.jpg` (a plain frame from the first 3 seconds, no text added),
`alt-text.txt` and `post-text.txt` (every platform caption and the title ideas). Never source clips, transcripts, settings or paths.

## Website endpoints

All need a signed-in session unless marked public. "Owner" means the person who made the video, or an admin.

| Method and path | What it does |
|---|---|
| GET `/login`, POST `/api/login`, POST `/api/logout` | Sign-in page, sign in (both public), sign out. |
| POST `/api/change-password` | `{current, new}`; the session carries on, other sessions end. |
| GET and POST `/api/local-reset` | "Forgot password?", this computer only (public, refused over the tunnel). |
| GET `/api/help` | Public: whom to ask for help (`HELP_CONTACT`). |
| GET `/healthz` | Public: liveness check (also the Docker health check). |
| GET `/api/me` | `{user, admin, local, help, show_tips}`; `show_tips` is true for an account that has made no video and never closed the first-run tips. |
| POST `/api/tips-seen` | She closed the three first-run tips: never shown to this account again (`out/tips_seen.json`). |
| GET and DELETE `/api/prefs` | Her saved choices (`denoise`, and `saved`: the remembered settings as plain lines for "Using your last settings" on the upload page); DELETE forgets them ("Back to normal"). |
| POST `/api/uploads` | Start an upload: `{clips:[{name,size}], voice:{name,size} or null, denoise, hints, format, private}`. |
| GET `/api/uploads/{job}` | How much of each file has arrived (resume). |
| PUT `/api/uploads/{job}/{slot}` | One piece (`?offset=`), at most 32 MB. |
| POST `/api/uploads/{job}/finish` | Check the files and the voice-over length, then start the video. |
| GET `/api/jobs/{job}` | `status` (`queued`, `running`, `done`, `failed`), `step`, `ahead`, `error`, `can_retry`; when done also `report`, `copy`, `ai_prompt`, `transcript`, `trust` (the result page's trust line from the saved `match/scores.json`: `{kind: estimate or none, estimated_available, estimated_missing, text}; estimates of available footage, not shown-timeline correctness`, null outside voice-over mode; `web/review.py`), `review_minutes` (her timed review, or null), `version` (1 for a first video), `previous` and `previous_file` (the version before, for Compare, only when it still exists and is hers). |
| DELETE `/api/jobs/{job}` | Delete one video; `?all=1` every version and its uploads. Owner. |
| POST `/api/jobs/{job}/retry` | Make it again from the saved files. |
| GET `/api/jobs/{job}/clips`, POST `/api/jobs/{job}/reorder` | Her clip names; remake in a new order `{order:[3,1,2]}`. |
| POST `/api/jobs/{job}/feedback` | `{changes, remember, comment, remake, by_hand_minutes}`: remake with new settings. Typed text is read by Claude (a keyword reader with no key). |
| POST `/api/jobs/{job}/review-time` | `{seconds, session_id?}` (1 s to 4 h; retries with the same session ID count once) from "Time my review": one session added to `out/<job>.review.json` (only the length is kept). Owner; finished videos only. |
| POST `/api/jobs/{job}/event` | `{kind: copy, download or posted; platform}`: one line in `out/events.jsonl`. |
| GET `/api/jobs/{job}/{name}` | A finished file (`final*.mp4`, `copy.json`, `ai_prompt.txt`); nothing else is served. |
| GET `/api/jobs/{job}/thumb.jpg` | A still from 1 s in. |
| GET `/api/jobs/{job}/longform`, GET `/api/jobs/{job}/long.srt` | Long video mode: the YouTube post for the whole long video (JSON) and its captions file. Owner. 404 for any other video. |
| GET `/api/jobs/{job}/postpack` | The post pack zip. Owner. |
| GET and POST `/api/jobs/{job}/posting` | `{status: draft, reviewed or posted; platform; date}`. |
| GET `/api/posting/week` | This week's count against her goal of 3 to 5. |
| GET `/api/posting/reminder.ics` | `?day=YYYY-MM-DD&time=HH:MM&title=`: a one-hour calendar file; nothing is sent. |
| GET `/api/videos`, GET `/videos` | My videos data and page. |
| GET `/api/formats`, GET `/api/ideas?n=5` | Video kinds with their "film this" lists; ideas (cached per person, so repeated taps cost nothing). |
| POST `/api/shotlist` `{text}` or `{job_id}`, GET `/api/shotlist/sources` | Shot list for what she plans to say (or an earlier video's voice-over words, her own videos only): `{shots: [{phrase, film, seconds}], tips, warnings, source: "ai" or "plain"}`. Claude with a key (her words only), a plain list from the things she names otherwise (`video_tool/shotlist.py`). Cached per person and text for 10 minutes. |
| GET `/api/demo`, `/api/demo/thumb`, `/api/demo/video/{n}` | The example video named by `out/_demo.txt`; `{available:false}` when there is none. |
| GET `/manifest.webmanifest`, `/sw.js`, `/assets/icon-192.png`, `/assets/icon-512.png` | Public: phone-app files. |
| GET `/admin`, `/api/admin/feedback`, `/api/admin/health`, `/api/admin/failures` | Admin: requests; health card (`disk_free_gb`, `jobs_total`, `jobs_failed`, `key_present`, `last_job_at`, `keep_awake`, `help_contact_set`); last 20 failures. |
| POST `/api/admin/feedback/{id}/resolve`, POST `/api/admin/users` | Admin: mark a request done, add a person. |

Security notes: the site sets its own security headers; session cookies slide (90 days since the last visit); `users.json` keeps a
`.bak` it recovers from; five wrong sign-ins for one name from one address pause that name for a minute. The Dockerfile runs with
`--no-proxy-headers`, so a visitor cannot fake an address, but behind a host's proxy every visitor then looks like one address.

## Tests

Unit tests are plain scripts: they print PASS or FAIL lines and exit non-zero on failure. No Claude calls, no video made.
Run each as `venv\Scripts\python tests\test_<name>.py`:

| Script | Covers |
|---|---|
| `test_engine.py` | retakes, opening check, uncertain words, warnings, hotwords, AI error wording, settings |
| `test_content.py` | formats, Fair Housing and fact checks, disclosure, contact notes, post pack, ideas |
| `test_backend_a.py` | accounts, delete, events, admin, prefs, hardening (in-process test client) |
| `test_backend_b.py` | upload checks, formats, ideas, post pack, posting log and week, reminder file, example video, phone-app files |
| `test_fixer.py` | one regression check per review finding that was fixed |
| `test_ops.py` | `check_setup.py` helpers, tunnel exit codes, `.bat`, Docker and ignore-file contents |
| `test_yt_report.py` | the YouTube report parsing and maths, offline |
| `test_highlights.py` | long video mode: picking moments (plain and with a fake Claude), cutting, the job with a fake pipeline, the `highlights` setting |
| `test_longform.py` | long video mode: chapters, the plain YouTube post, the Claude path with a fake client, `long.srt`, the post pack |
| `test_longvideo_web.py` | long video mode on the website: upload checks, estimate, progress, a finished Shorts job, a restart |

Last run (Windows 11, 7 Oct 2026): all seven of the first group exit 0 with no FAIL lines. The three long video tests were run again on 10 Oct 2026: all exit 0.

Browser tests (Playwright, real Chromium) drive the site the way the broker would. `venv\Scripts\python tests\e2e_site.py` starts its own
copy of the site on port 8011 (set **`E2E_PORT`** for another port, so runs can go side by side) with a throwaway data folder, a
test account and a blank API key. The base flow signs in, makes the recipe video, remakes it, opens My videos and uploads a
broken clip (about 3 minutes); it then runs every `tests/e2e_part_*.py` file. Options:

- **`E2E_ONLY=<part>`** runs one part only, for example
  `set E2E_PORT=8072` and `set E2E_ONLY=backend_b` then `venv\Scripts\python tests\e2e_site.py`
  (parts: `backend_a`, `backend_b`, `content`, `engine`, `fixer`, `fixer_long`, `longvideo`, `screens_pages`, `screens_result`, `screens_upload`, `example`; also `autosort` and `results`).
- `--full` also makes the 11-clip house tour and the 150-second talking video (about 10 more minutes).
- `--ai` tests the Claude features with the real key instead (about 5 cents).
- `--show` shows the browser.

To write a new part copy `tests/e2e_part_example.py`; a part must work on its own. Screenshots go to `tests/shots/`.

### What was tested, and what was not

Tested for real: every unit test script above; the browser parts in a phone-sized Chromium; real videos from the three samples
(recipe, tour, talking tips) including retake cutting, the opening check, clip moving and the three-part split; the upload
(including a voice-over recorded by Chrome with a fake microphone), the post pack zip, delete, password change and the sign-in limit.

**Not tested** (say so in front of the client):
- A real phone: iPhone Safari and Android Chrome behaviour, "Save to my phone", Add to Home Screen, and **recording a voice-over
  with MediaRecorder on iPhone Safari** (only Chrome's fake microphone was used).
- The real 150 MB upload from a phone on cellular through the Cloudflare tunnel (step 3 of the start-of-day checklist).
- A Mac, and a clean Windows computer that has never had Python or ffmpeg.
- The **Docker image**: never built or run.
- The YouTube report against real Google data.
- Clip matching on footage other than the three samples. Claude's scores vary a little from run to run.

## Decisions still open (for the maintainer and the broker)

1. **Always-on host.** The free tunnel gives a new link on each restart and only works while this laptop is on. Choose: a small cloud
   server (the Dockerfile, untested), a named tunnel with a domain, or keep it laptop-only.
2. **Length cap.** 60 seconds (now) or up to 3 minutes (what YouTube counts as a Short). `MAX_SECONDS` in `video_tool/config.py`.
3. **Clip moving** (`auto_order`): keep it on, or turn it off so her order is never changed.
4. **Example compliance line.** The exact disclosure or brokerage line for `prompts/disclosure.txt`. Until it is filled in nothing is added.
5. **Who supports it after the class**, and who pays for the Claude credit and hosting.
6. Her website address in `prompts/contact.txt` (the line is switched off until she confirms it).

## More guides
- **[HANDOVER.md](HANDOVER.md):** start/stop, accounts, keys, backups, what to do when it breaks, open decisions.
- `venv\Scripts\python check_setup.py` checks that a computer is ready (Python, ffmpeg, packages, models, Claude key, accounts, her writing samples, music, the laptop lid and sleep settings, the help line, her website in `prompts/contact.txt`, free disk; it changes nothing); `maintain.py` handles disk space and backups; `tunnel.py` shows the new link.

## YouTube report (class metrics)

`venv\Scripts\python -m video_tool.yt_report --demo` writes `out\youtube_report\demo.html` with made-up
numbers to show the layout: Shorts vs long-form totals, before vs with the tool, videos per week against
her goal, every video, and how many of the tool's titles she posted unchanged. Real data is read-only and
needs one-time Google setup plus the broker's approval: see `SETUP_YOUTUBE.md`. Not yet run against Google;
`tests\test_yt_report.py` checks the parsing and maths offline.

## Installing on another computer

Double-click **Install Video Maker.bat** on a Windows 10/11 computer: it installs Python, ffmpeg, the
packages at the tested versions and the model files, makes the settings file and a sign-in, then runs
`check_setup.py`. Safe to run again. Step-by-step guide, requirements and what has and hasn't been
tested: `INSTALL.md`.

## Rule: no AI-generated media
Never add AI-generated footage, voice or images to this tool; her videos are her real clips and her real voice. Before any change that could, re-check the YouTube, TikTok and Meta AI-label rules first.

## Clip order (automatic)

She never has to number or sort clips. A phone writes the moment it started (or finished) filming into every clip,
and `video_tool/clip_order.py` sorts by that moment:

- **Website:** when an upload finishes, the clips are renumbered 01, 02, 03... in filming order (`web/app.py _put_in_order`),
  unless she used the arrows herself (the page then sends `manual_order` and her order is kept exactly). The result page
  says "Put your clips in the order you filmed them" (`report.json` `clip_order`), and a manual "change the order" remake
  clears that note.
- **Command line:** a folder whose clips are not numbered 01, 02... (or are named like camera timestamps) is sorted the
  same way instead of stopping. Numbered clips keep their numbers.
- **Never guessed:** if any clip has no time, all clips carry the same time (an export), or the times overlap in a way
  filming can't (the check accepts times that mean either the start or the end of a clip), the order she gave is kept
  (the page's own order: filming order by the file dates when those are trustworthy, else file-name order).
- In voice-over mode the clip matching (`auto_order`) may then move a clip to where she talks about it.
- Tests: `venv\Scripts\python tests\test_clip_order.py` (real tour clips shuffled + odd files) and the browser part
  `E2E_PORT=8111 E2E_ONLY=autosort venv\Scripts\python tests\e2e_site.py`.

## Results for the class report

The Team page (`/admin`, admin accounts) has a **Results for the class report** card and a spreadsheet download
(`/api/admin/results`, `/api/admin/results.csv`). From the videos and the copy/download/posted taps (never her words) it shows
videos and remakes, how many she marked as posted, videos per week against her goal of 3 to 5, the tool's build time,
the time from "ready" to her first tap (only when within 4 hours), her own estimate of editing by hand, the review she timed herself with "Time my review"
on the result page (`timed_review_min`: all versions of a video added up; the median and how many videos it rests on), and the
minutes saved per video, each with how many videos it rests on. The maths is `video_tool/results.py`
(`venv\Scripts\python tests\test_results.py`); the browser part is `E2E_ONLY=results`.

## Look and colours (Example Realty)

The colours follow Example Realty's own site (the brokerage's site): its sky blue `#6CAEDF` as the brand colour, a deeper blue
`#2C76AE` for buttons and links so white text stays readable (about 4.9 to 1), charcoal text `#1F1F1F`, slate `#54595F` for
secondary text, white cards on a cool light background, and a navy dark mode. They live in the `:root` token blocks at the top of
each page (`--accent`, `--brand`, `--bg`, `--card`, `--ink`, `--muted`, `--line`); the app icons, the manifest and the
YouTube report use the same values. Her logo is not used.

## Motion (quiet, optional, one file)


Smooth wheel scrolling (Lenis) and small GSAP animations, on the upload/result page and My videos only, and only for:
the processing screen (heading, the step that becomes active, the tick of a finished step), result cards and Short/clip
players entering when a result loads, and the week card, its sentence and its reminder buttons.

- **Files:** `web/motion.js` (ours), `web/vendor/gsap.min.js` (GSAP 3.15.0, "no charge" licence), `web/vendor/lenis.min.js` and
  `lenis.css` (Lenis 1.3.26, MIT), served from exact names under `/static/`. `package.json` exists only so
  `npm install && npm run vendor` can refresh the two vendor files; the site needs no build and the broker's computer needs no Node.
- **How:** `motion.js` watches the page (MutationObserver) and tweens what is already there, so no page logic, layout, text or
  JSON changed. Every tween clears its inline styles when it ends. Lenis is created once and driven by the GSAP ticker; touch
  scrolling stays the phone's own.
- **Off switch:** when the visitor prefers reduced motion nothing animates and Lenis is not started. To remove all of it,
  delete `web/motion.js` and the four tags near `</head>` in `web/index.html` and `web/videos.html`.
- **3D scene (processing screen):** `web/vendor/scene3d.js` (built from `web/src/scene3d.src.js` with three.js 0.186 and esbuild by
  `npm run build:3d`; 560 KB, loaded only when the processing screen first shows). Her clips orbit a phone and are drawn into it
  as the progress bar fills; when the result is ready the phone spins once and the scene is taken down. It is skipped with
  reduced motion, with no WebGL, on very low-power computers (2 cores or fewer) and in data-saver mode, pauses when the card or
  tab is not visible, and frees its WebGL context when it ends.
- **Four different scenes, one for each place** (all in `web/src/scene3d.src.js`, same safety rules): the processing screen
  shows her clips orbiting a phone and being drawn into it; the **sign-in page** shows a little house with warm windows and a
  brass key circling it; the **start page** (while no clips are chosen, gone once she picks some, back after "Start over")
  shows what the tool does as a picture: clips go in on the left, captions are made in the middle, a finished card with its title comes out on the right, and light flows through all three; and the **result page** opens with the finished video's own first picture
  (`/api/jobs/<id>/thumb.jpg`) on a card that spins in while YouTube, TikTok, Instagram and Facebook badges fly into orbit
  and a few sparks burst, then it folds away so the video is right at the top. `scene3d.js` is the one file the sign-in
  page may fetch signed out (`PUBLIC` in `web/app.py`).
- **Extra polish:** softer expo easing on entrances, a ring that opens around a finished step's tick, a glow and slow shine on the
  progress bar, and the My videos numbers counting up the first time they show.
- **React Bits** was not added: it is React-only and this site has no React or bundler. The staggered list entrance is
  written directly with GSAP instead.
- **Tests:** `E2E_PORT=8131 E2E_ONLY=motion venv\Scripts\python tests\e2e_site.py` (served files, each animation ends clean,
  repeated polls do nothing, wheel scrolling works, reduced motion is respected).

### How the clip-matching accuracy is stated (and why)

The first headline, "82% of the video shows what she says", divided by the time of the phrases that at least one clip can
illustrate and left the rest out. It answers "when a suitable clip exists, does the planner put it on screen?". It does not
say how much of the narration is shown properly. `tests/matching_accuracy_report.py` recomputes four separate figures from
the saved finished-video timeline of the one measured tour (11 clips, 19 phrases, 45.9 s, an answer key made by a model):

| Figure | Claude (finished video) | Her own order, even split |
|---|---|---|
| Footage coverage: narration some clip can illustrate | 58.4% (26.8 s of 45.9 s); 41.6% (19.1 s) has no clip that shows it | the same footage, the same 58.4% |
| Accuracy during supported narration | 81.7% | 24.3% |
| Accuracy across all narration (coverage x the line above) | 47.7% | 14.2% |
| Contradictory visuals (a clip the key says contradicts the phrase), supported / all narration | 2.1% / 1.2% | 34.5% / 20.2% |

Limits that stay attached: one tour; the key is a model's labelling, not a person's; there are no independent held-out houses
(splitting phrases inside the same tour shows nothing about a new house); "supported" is the key's judgement of what the
filmed clips can show.

### Finished-check limits (Phase 1 follow-up)

`final_check` warnings remain advisory and render with the other result-page warnings. A check that cannot run can
return no warnings; absence of warnings is not proof of a clean video. Shorts run through the child pipeline and its
finished checks; their warnings are gathered into the parent report. Multi-clip own-audio source attribution is not
available after pause/retake cuts, so those warnings do not name the source clip. The hook accepts an offset for a
part's local clock when a source timeline is supplied.

Repeated-footage comparison samples at most 2400 frames: each float32 square array is at most 23,040,000 bytes;
several arrays and temporaries coexist. This caps the quadratic comparison allocation, not total process memory:
frame decoding and caption masks still grow with duration. Longer inputs use coarser sampling and can miss brief
repeats. Runtime and false-alarm rate on the full saved-job archive remain unmeasured.

### How to run an evaluation (many houses, person-made keys)

**What to film.** 3 to 5 tours of *different* houses (not two takes of one house), each a normal voice-over video made with
the tool. Different layouts, sizes and lighting matter more than more clips of one house. Keep each finished job folder.

**Who marks.** Two people, each marking alone (not the broker unless she wants to; never in front of each other). For each job:

    venv\Scripts\python tests\answer_key_tool.py out\<job> --rater A [--house "12 Elm St"] [--clips <her clips folder>]

then open the address it prints (http://127.0.0.1:8321/). For every phrase she says, each clip is marked **Shows it**,
**Wrong room** (showing it would contradict her words) or **Neither**. It saves `answer_key.A.json` next to the job as you go,
works offline, needs no key and listens on this computer only. The second person runs the same with `--rater B`.
Give the same `--house` to two jobs of one house so they count as one house.

**Score.**

    venv\Scripts\python tests\evaluate_matching.py out\house-1 out\house-2 out\house-3 --csv evaluation.csv [--holdout "house-3"]

It reads only saved files and calls nothing. For each job, rater and timeline ("finished" = the saved video, which is Claude's
plan when the job ran with a key; "her_order" = her clips in filmed order, the no-Claude plan; plus any `timeline.<name>.json`
or `alternatives\<name>.json` saved next to the job) it prints the four figures, then the pooled figures over houses with a
95% bootstrap interval over houses (fixed seed `--seed 0`, `--boot 2000` draws), the difference from her own order, and the
two raters' agreement. The earlier one-tour model-made key converts with `--import-legacy <key> --job out\engine-A-claude
--clips samples\tour --out-dir <folder>` and then reproduces the table above (passed as `--keys <folder>`).

**What each number means.**
- *Footage coverage*: the share of narration that some filmed clip could show. Low means film more, not match better.
- *Accuracy when supported*: when a fitting clip exists, how much of that time it is on screen. The matcher's own job.
- *Accuracy across all narration*: how much of everything she says is shown properly (coverage x the line above).
- *Contradictions*: time a clip of the wrong room is on screen while she talks about another. The worst error; keep it near 0.
- *Interval*: the range the pooled figure would plausibly move to with other houses like these. With 3 houses it is wide;
  if Claude-minus-her-order includes zero, these houses do not show Claude is better.
- *Agreement* (percent and Cohen's kappa over every clip x phrase): below 0.4 the key is too unsure to trust the figures;
  write down how the raters read "shows it" and mark again. Figures are printed for each rater separately.
- *Held-out houses*: if you change the matcher while looking at some houses, quote the figures for the houses you did NOT
  look at (`--holdout`, or a fixed one-in-three pick when there are 3 or more houses).

**What it does NOT show.** Whether the video looks or sounds good, whether the broker likes the cut, the hook or the captions, or
how it does on houses unlike the ones filmed. It measures picture-against-words on a 0.05 s grid, nothing else.

### Match labels and saved scores
The shot review uses an uncalibrated threshold of 7/10 for "possible match". It is not a probability or human-verified confidence. `/shots` includes `shown_score` (maximum sampled score near this phrase in the shown clip) and `best_score` (best saved score for that phrase anywhere); both are null without scores. Raw scores are kept in API/test evidence; the UI uses conservative words without numeric scores. Original saved scores are never rewritten. Independent human answer keys are still needed to calibrate labels.

### Ducking and shot-list verification (21 Oct 2026, phase2 branch only)

`music_duck` stays OFF by default. The upload checkbox and result-page Music button send a real boolean;
chunked uploads, legacy uploads, remakes and remembered audio preferences carry it through the existing settings.
Ducking requires a music bed and runs before the existing encoded-peak guard. No other provider is introduced.

Shot-list requests require text or an owned job ID. Concurrent identical requests share the process-local cached
answer (10 minutes); different server processes/restarts do not share that cache. Returned filming instructions use
the existing plain templates, not unchecked model prose. Both paths recognize the existing subject vocabulary;
unknown nouns and complex/ambiguous negatives still need her review. Explicit negatives are omitted, and accepted
AI rows are restored to narration order. The browser clears stale shots when a new request fails.

See `AUDIO-VERIFICATION-CODEX.md` for the measured off/on matrix, test results and remaining human checks.
The -14 LUFS voice target was NOT reached exactly on these real inputs: recipe measured -14.8 and tour -15.6
in both modes. Ducking did not change that baseline; the shared leveller was not redesigned in this lane.
Keep ducking off until a person completes a blind listening comparison. No claim of improved perceived quality.

### Review helpers: estimates and explicit timing
The result trust line describes saved AI scores as an estimate of available footage. It does not measure shown-timeline correctness or display exact match counts. Its API fields are `kind`, `estimated_available`, `estimated_missing`, and `text`; unavailable or invalid scores have null estimates. No-key fallback describes clip order and available length, never promises equal time.
`POST /api/jobs/{id}/review-time` accepts seconds (1 to 14400) and optional `session_id` (1 to 80 letters, digits, underscore or hyphen). Retrying the same session does not add it twice. The browser keeps stopped unsaved time for retry, including after reload when browser storage is available. Active timing has an in-memory fallback if storage is blocked, but cannot survive closing that page. Timer files are replaced atomically; failed saves return a plain 503. Short sessions count in reports; family totals use three decimal places in minutes. Timing remains voluntary and separate from inferred event-based review duration.
First-run tips are dismissed per account only after the server confirms saving. Failed dismissal stays visible for retry. Saved preferences/reset reuse existing per-account routes.

## Horizontal YouTube output

Upload choice `orientation`: `vertical` (default, unchanged 1080x1920 under 60 seconds) or `horizontal`
(1920x1080, 30 fps). `tall_clips`: `bars` (default, upright/square pictures shown whole) or `fill`
(cut top/bottom without stretching). Both are per video, validated, preserved on remakes and never remembered.
The chunked `/api/uploads` JSON and legacy multipart `/api/jobs` accept both fields. Horizontal and highlights
cannot be requested together through the website; highlight child renders always use vertical orientation.

Horizontal talking footage accepts at most 899.5 seconds total SOURCE duration, leaving a half-second encode
margin under 15 minutes. Longer sources are rejected before normalization/rendering with instructions to split
recordings manually. Horizontal voice-over may be at most 899.5 seconds AFTER trimming pauses; it is rejected
if longer. Automatic horizontal multipart output is not supported. Vertical multipart behaviour is unchanged.

Landscape clips fill by default. Explicit framing `fit` shows EVERY talking clip whole, including landscape.
For upright/square talking clips, bars win by default; choosing fill on a remake sets `tall_clips=fill` and uses
face-aware fixed crop for `auto`, centred crop for `fill`. `fit` sets `tall_clips=bars`. Voice-over clips use a
centred crop when fill is chosen. No moving crop or stretching is introduced.

Horizontal captions use a 1920x1080 ASS canvas with scaled size and lower-third margins. The result's `longform`
field points to the existing YouTube kit and SRT routes, also included in the post pack. The kit describes the
single rendered output. Horizontal uploads start with auto framing rather than a remembered vertical framing preference. Copy titles/description come from that kit, avoiding a separate short-form AI writer.
For schema compatibility the YouTube description occupies `captions.youtube_shorts`, but `copy.orientation`
is `horizontal`: the page and pack label it YouTube, without adding a Shorts hashtag. The kit is preserved on
remakes when the transcript and caption timing are identical; changed timing rebuilds chapters. Alt text and result notes describe horizontal output.

Verification: `tests/test_horizontal.py`, `tests/test_horizontal_render.py` (tour, wide, square; runs in run_all),
and `E2E_ONLY=horizontal` with a port in 8341-8349. No AI keys are needed.
