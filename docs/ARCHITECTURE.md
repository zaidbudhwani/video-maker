# How the Video Maker works

A 10-minute read. After it you should be able to explain the project, find any piece of it, and make a change safely.
For installing, see `../README.md`. For every setting, field and endpoint, see `REFERENCE.md`.

## What it does, in one paragraph

A real estate broker films short clips on her phone and (often) records a separate voice-over. She drops them in. The tool
makes one vertical 1080x1920 video (under 60 seconds) with plain white burned-in captions, and writes three title ideas, a
description and a caption for each platform, in her voice. She reviews it, can ask for changes ("bigger captions", "fix a
word"), and posts it herself. The client's rules: her raw style (no transitions, zooms or effects), nothing posts by itself,
and she should spend minutes reviewing, not hours editing.

## The three modes

The mode is chosen from what is uploaded (`video_tool/pipeline.py`, `main`):

| Mode | Input | What happens | Main code |
|---|---|---|---|
| **Voice-over** (b-roll) | several clips + one voice file | The voice sets the length. Clips are joined, trimmed to fit and timed to what she says. | `assemble.py`, `match.py` |
| **Talking to camera** | one or more clips, no voice file | The clips' own sound is used. Pauses and repeated takes are cut, her face is kept in frame, long videos are split into parts. | `own_audio.py`, `facecrop.py` |
| **Long video** (Shorts from a long video) | ONE finished long video, setting `highlights` 1 to 5 | The best moments are chosen and cut out as Shorts, and a YouTube post for the long video is written. The long video itself is never re-rendered. | `highlights.py`, `longform.py` |

## The pipeline (voice-over mode; the others are variations)

`python -m video_tool.pipeline <folder>` prints these steps; the website maps them to its progress bar.

| Step | What | Code |
|---|---|---|
| 0 | Check every clip opens; put clips in the order she filmed them (no numbering needed) | `assemble.check_clip`, `clip_order.py`, `inputs.py` |
| 1 | Speech to text with word timestamps (Whisper, **runs on this computer**) | `transcribe.py` |
| 2 | Cut pauses over 0.7 s and filler words | `silence.py` |
| 3 | Make every clip the same size/fps/colour (phone HDR to normal colour), skip shaky starts, join, fit to the voice | `assemble.py`, `deadstart.py` |
| 3b | **Clip matching**: an AI looks at still frames and scores every frame against every phrase she says; a planner then times the cuts so the right footage is on screen, and may move a clip to where she mentions it | `match.py` (scores, planner), `ai.py` |
| 3c | Quality checks (shake, blur, darkness, audio) become plain warnings, fixed where possible | `quality.py` |
| 4 | Captions: 2 to 4 words a line, burned in | `captions.py` |
| 5 | Optional music bed at -22 dB, optional noise removal | `music.py`, `denoise.py` |
| 6 | Titles, hook, description and per-platform captions in her voice, checked by code (Fair Housing, length, hype words) | `writer.py` (AI), `local_copy.py` (no-AI fallback), `formats.py` |

Finished files and all working files go in `out/<job id>/` (`final.mp4`, `report.json`, `copy.json`, `transcript.txt`,
`words.json`, `captions.ass`, `match/scores.json`, ...). `report.json` is the record of what the tool did and is what the
website's result page is drawn from.

## The website

`web/app.py` (FastAPI, one file) serves plain HTML pages with plain JavaScript: no build step for the pages themselves.

| Page | File | What |
|---|---|---|
| Upload and result | `web/index.html` | pick clips (chunked, resumable upload), watch progress, review, ask for changes, copy captions |
| My videos | `web/videos.html` | every video, posting record, weekly pace against her goal |
| Sign in | `web/login.html` | per-person accounts (`web/auth.py`) |
| Team page | `web/admin.html` | feedback, accounts, health, failed videos, class results |

Jobs run one at a time in a background thread (`_run` in `web/app.py`), calling the same pipeline. A "remake" re-runs the
pipeline with changed settings and reuses the saved transcript and clip scores, so it is much faster.
`web/interpret.py` turns a typed note ("captions are too small") into setting changes.
`web/motion.js`, `web/src/scene3d.src.js` and `web/vendor/` are the page's animated 3D scenes (built with `package.json`).

## Settings, accounts and where things are stored

- **Settings**: `video_tool/settings.py` (`DEFAULTS`, with limits so a button cannot break the brief's rules). Each person's
  saved choices are a `prefs.json` in their own folder; a one-off remake can override them.
- **Fixed constants** (size, fps, caption limits, model names): `video_tool/config.py`.
- **Secrets** live in `.env` (copied from `.env.example`): `ANTHROPIC_API_KEY`, `HELP_CONTACT`, optional
  `ANTHROPIC_WORKSPACE_ID`, `PASSWORD_OFF` (testing only), optional `OPENROUTER_API_KEY` (an experiment).
- **Accounts**: `users.json` and `secret.key` (never committed). Manage with `manage_users.py`.
- **Data folder**: everything is under the project folder unless `ALEX_DATA_DIR` points elsewhere (a hosted copy). `ALEX_HOSTED=1`
  makes sign-in mandatory (the public link always is).
- **The client's writing** for the AI to imitate: `prompts/examples.md`; her disclosure line: `prompts/disclosure.txt`;
  words to listen for: `prompts/vocabulary.txt`; phone/website on file: `prompts/contact.txt`.

## What goes to an outside service (the privacy map)

| What | Where | When | Switch |
|---|---|---|---|
| Her words (the transcript text) | Anthropic (Claude) | to write titles and captions | no key = fallback copy, nothing sent |
| The words she types in "Plan my shots" (or an earlier video's transcript she picks) | Anthropic (Claude) | to list the shots her words need | no key = a plain list made on this computer, nothing sent |
| Small still pictures from her clips | Anthropic (Claude) | to time clips to her words | setting `match_clips` off, or "Keep my pictures on this computer" on an upload |
| Speech to text | **nowhere**: Whisper runs locally | always local | n/a |
| Her clips and finished videos | **nowhere** | stay on this computer; the public link only serves them to signed-in people | n/a |
| Optional experiment: pictures or short pieces to OpenRouter (Qwen) | OpenRouter | only if `match_engine` is set to a Qwen mode and a key exists | off by default |

**House rule (hold set 9 Oct 2026, eased 19 Oct 2026): the client approved still pictures and her words going to Claude. Nothing of
hers goes to any other outside service (OpenRouter/Qwen, any new vendor) without a fresh yes.** A client's listing footage still
gets the "Keep my pictures on this computer" box. The hold's history is in `../AGENTS.md`.

## Installing and running (the Windows scripts)

| File | Purpose |
|---|---|
| `Get Video Maker.bat` | One file to send someone: installs Git and GitHub CLI, signs in, downloads this repository, runs the installer, adds Desktop icons. Runs again as an updater. |
| `Install Video Maker.bat` / `install.ps1` | The installer (8 steps: checks, Python 3.11, ffmpeg and cloudflared, packages from the lock file, model files, speech model, `.env` and an account, checker). Idempotent. `-Unattended` for no prompts. |
| `check_setup.py` | "Is this computer ready?" with the fix for each problem. `--api` tests the Claude key with one tiny call. |
| `Start Video Maker.bat` / `Start Website.bat` | Run it on this computer / with a public link (`tunnel.py` catches the link). |
| `Install Autostart.bat`, `Schedule Backup.bat`, `maintain.py` | Keep it running on a spare computer: start with Windows, weekly backup, disk housekeeping. |
| `Update Video Maker.bat` | `git pull` and re-run the installer. |

Packages are pinned in `requirements.lock.txt` and installed with `--no-deps` (letting pip resolve them breaks mediapipe;
see `../AGENTS.md`, "Known sharp edges").

## Tests

| Command | What |
|---|---|
| `venv\Scripts\python tests\run_all.py` | Every `tests/test_*.py` unit suite, one summary. No AI calls, about 2 minutes. |
| `venv\Scripts\python tests\e2e_site.py` | The whole website in a real browser (Playwright) on its own port with throwaway data. 12 to 15 minutes. **Run alone**: a second heavy run at the same time causes false failures. `E2E_ONLY=<part>` runs one `tests/e2e_part_<part>.py`; `--full` adds the tour and a talking video; `--ai` uses the real key (about 25 cents; never on the client's files). |
| `tests/compare_match_engines.py` | The Claude-vs-Qwen clip-matching comparison tool. Refuses to send anything without `--send-footage-ok`. |

## Where to change what

| I want to change... | Edit |
|---|---|
| How long a pause must be to be cut, caption size, music level | `settings.py` / `config.py` |
| How titles and captions are written, the word lists | `writer.py`, `local_copy.py`, `formats.py`, `prompts/` |
| Which clip is shown during which words | `match.py` (`phrases`, `solve`, `better_order`, `plan`) |
| How a clip is cropped or framed | `assemble.py` (b-roll), `facecrop.py` (talking) |
| A page's look or text | `web/*.html` (the plain-English wording matters: she is not technical) |
| An endpoint or a rule about who can see what | `web/app.py`, `web/auth.py` |
| The installer | `install.ps1`, `Get Video Maker.bat`, `check_setup.py` |

## Known limits (be honest about these)

- Windows only; the launchers and the noise remover are Windows builds.
- The install has run from nothing on one computer. A clean computer, the GitHub sign-in step, iPhone behaviour and a 150 MB
  cellular upload are **not yet proven**.
- The free public link changes at every restart and works only while the computer is awake.
- Clip matching is an AI's reading of still pictures. On the one house tour measured (a model-made answer key, no independent
  held-out houses) the figures are kept apart on purpose (`tests/matching_accuracy_report.py` recomputes them from the saved
  timeline): **footage coverage 58%** (the narration that at least one filmed clip can show; the other 42%, about 19 s, is a
  filming/script issue, not a matching one); **accuracy during supported narration 82%** (her own order: 24%); **accuracy across
  all narration 48%** (her own order: 14%); **contradictory visuals 2% of supported and 1% of all narration** (her own order:
  35% and 20%). "82% of the video shows what she says" is therefore too broad. One tour is not enough to claim more.
- The human-time saving (hours down to about 20 minutes) is the client's goal and has **not** been measured yet.
