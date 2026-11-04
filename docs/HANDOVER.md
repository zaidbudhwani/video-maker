# Handover: running and fixing the Video Maker

For whoever looks after this next (a teammate, or a successor after the class). Plain steps, no guesswork.

## What it is
A website that turns the broker's phone clips (plus an optional voice-over) into vertical videos for YouTube Shorts, TikTok, Instagram
and Facebook Reels, with captions, titles and a caption for each platform. It runs on **one Windows computer** (the maintainer's laptop). Her
videos, accounts and settings live in this folder. Nothing posts automatically.

## Start it, stop it
| I want to | Do this |
|---|---|
| Let the broker use it from her phone | Double-click **Start Website.bat**. Leave the window open and the computer on. The link is shown in a banner, saved in `current-link.txt` and copied to the clipboard. A free tunnel gets a **new link every start**. If the website crashes or the Wi-Fi drops, the window starts the website / the tunnel again by itself after 5 seconds and says so: a restarted tunnel makes a **new link**, so send the broker the new one. The website's own messages are in `out\server.log`. |
| Use it only on this computer | Double-click **Start Video Maker.bat** (no sign-in needed here). |
| Stop it | Close the windows. |
| Start it by itself after a reboot | Double-click **Install Autostart.bat**. Only useful with a link that never changes (see below). **Remove Autostart.bat** undoes it. |

`Start Website.bat` first stops any older copy of the site and any half-finished video job left over from it, so a restart always runs the newest code
(a video that was being made is shown to the broker as "didn't finish" with a *Make it again* button; her files are kept).

## Start-of-day checklist (before the broker is going to use it, and before any demo)
1. The laptop is **plugged in** and the **lid is open** (or Windows' lid action is set to *Do nothing*: Control Panel > Power Options > *Choose what closing the lid does*). A closed lid or a sleeping laptop stops the website. `check_setup.py` warns about this.
2. Double-click **Start Website.bat** and wait for the banner with the link.
3. From a **phone on cellular** (Wi-Fi off), open the link, sign in and **upload one 150 MB clip**. This proves the real route works end to end (uploads go up in 8 MB pieces, well under Cloudflare's 100 MB limit, but this has not yet been tried on the live link: note the result here when you do).
4. Send the broker the link (it is in `current-link.txt` and on the clipboard). Agree with her which hours the laptop will be on.

## Monthly check (about 10 minutes)
1. `venv\Scripts\python check_setup.py --api`: every line fine, and the Claude key works (it needs credit).
2. console.anthropic.com: the credit balance is not close to zero, and read any model-retirement emails (see the model note below).
3. `venv\Scripts\python maintain.py usage`: disk space is fine; `maintain.py slim --yes` frees working files.
4. `out\backup.log` shows a recent backup, and a zip from this month exists in `Documents\video-backups` (copy it to your own private OneDrive).
5. Open **/admin**: no pile of failed videos or unread feedback.
6. Make one real test video from a phone clip and watch it all the way through.
7. `Update Video Maker.bat` if there is a new version (then repeat step 1).

## Check it is healthy
```
venv\Scripts\python check_setup.py          # everything installed? what to fix?
venv\Scripts\python check_setup.py --api    # also tests the Claude key with one tiny call
```

## Accounts
- Add or reset someone: `venv\Scripts\python manage_users.py add NAME` (add `--admin` for an admin), `passwd NAME`, `remove NAME`, `list`.
- Forgot your password **on this computer**: the login page has a *Forgot password?* button that only works here. Over the internet there
  is no reset: ask an admin. Changing a password signs that person out everywhere.
- The `/admin` page lists open feedback and failed videos.

## Secrets (the `.env` file, never in git, never in a chat or email)
- `ANTHROPIC_API_KEY`: the Claude key. **To rotate it:** console.anthropic.com → API Keys → Create Key (pick a workspace) → paste it in `.env`
  → check with `check_setup.py --api` → delete the old key. Do this if a key was ever pasted anywhere.
- `ANTHROPIC_WORKSPACE_ID`: only if the key says "not scoped to a workspace."
- `CLAUDE_MODEL`: the model name (default `claude-sonnet-5`, in `video_tool/config.py`). When Anthropic retires a model, set this to the new one, run `check_setup.py --api` **and make one real video with the AI on**. **Model-id trap:** `web/interpret.py` retries with `tool_choice` auto if a model refuses a forced tool, but `video_tool/writer.py` (titles and captions, and the film ideas) still forces a specific tool. Some newer models, Sonnet 5.5 among them, can refuse that. The video is still made (plain titles, a note on the page), but the AI copy is lost, and the plain `check_setup.py --api` message would not show it. Until `writer.py` also retries with auto, stay on `claude-sonnet-5` and, after changing the model, make one real video with the AI on. It is listed as retiring no sooner than 2027-06-30: check Anthropic's deprecations page, and make sure the Anthropic account email is an address that will still exist then (the warning email goes there).
- `APP_PASSWORD`: creates the first `alex` account on a fresh install.
- Do **not** zip the project folder to hand it over: it would include `.env`, `users.json`, `secret.key` and `local-login.txt`.
  Use `git archive --format=zip -o alex-tool.zip HEAD` instead (it only includes what is committed).

## Long video mode (Shorts from a long video)
the broker's email of 2 Oct 2026 asked for YouTube first (long video and Shorts), the same vertical clips reused on TikTok and Reels with a
caption for each, and her Facebook CRM auto-posts kept out of the tool. So the upload page has a choice: **A video from my clips** or
**Shorts from a long video**.

- **She does:** picks Shorts from a long video, adds one finished long video (over 75 seconds), picks how many Shorts (1 to 5, starts at 3),
  taps **Make my Shorts**. She can leave the page; the result is under **My videos**.
- **She gets:** the Shorts (25 to 58 s each, captions burned in, face-centred crop, three titles and a caption per platform) and a YouTube
  post for the long video: title ideas, description with chapters, tags and a captions file (`long.srt`). The long video itself is never
  changed. All of it is in the Post pack zip. She posts everything herself.
- **How long:** seconds = 0.22 x the video's length + 60 per Short + 20 (the formula in `web/app.py`, measured on this laptop). About 3 minutes
  for the 150-second sample with 2 Shorts, 6 for a 10-minute video with 3 Shorts, 10 for a 30-minute video with 3 Shorts. Longer videos were
  not timed separately. One video at a time (the render lock). A run is stopped after 2 hours; the site refuses over 4 hours.
- **Cost:** only transcript text goes to Claude: the picks and the long video's post add a few cents on top of about 2 cents per Short.
  With no key it all still works with plain, no-AI picks and a plain post (`source: "fallback"` in `longform.json`).
- **Footage rule (hold 9 Oct 2026, eased 19 Oct 2026):** the broker said yes to still pictures and her words going to Claude (relayed by the maintainer), so her
  real videos may use the Claude key. Nothing of hers goes to any other outside service (OpenRouter/Qwen, any new vendor) without a fresh yes.
  Long video mode still sends only transcript text. A client's listing footage gets the "Keep my pictures on this computer" box.
- **If it fails:** the log is `out\<job id>.log`; a Short that fails is left out with a plain note, and the page says how many were made.
  Leftover little folders from a killed run (`<job id>-h1`...) are cleaned up when the video is deleted or made again.
- **Files and routes:** `report.json` (`mode: "highlights"`, `highlights`, `longform`), `copy.json` (`parts`, one per Short), `longform.json`,
  `long.srt`; `GET /api/jobs/{id}/longform` and `/long.srt`. Shapes are in REFERENCE.md ("Long video mode").
- **Tests:** `venv\Scripts\python tests\test_highlights.py`, `tests\test_longform.py`, `tests\test_longvideo_web.py`, and the browser part
  `E2E_ONLY=longvideo` (with `E2E_PORT=<port>`) of `tests\e2e_site.py`.

## What the AI costs
About 2 to 10 cents per video (titles and captions about 2 cents; matching clips to her words about 8 cents on a long tour, twice that if she turns on "Double-check the clip matching"). Remakes reuse
earlier results for free. Speech-to-text runs on the computer, free. A video takes about 2 to 4 minutes (talking videos longer).

## Looking after it
- **Disk space:** `venv\Scripts\python maintain.py usage` shows where it went. Working files of videos older than 14 days are tidied automatically
  when the site starts (set `ALEX_AUTO_SLIM=0` to stop that). `maintain.py clean --days 90 --yes` deletes whole old videos.
- **Backups:** `venv\Scripts\python maintain.py backup` (accounts, settings, titles, history; not `.env`). Keep the zip somewhere private (it holds password hashes).
  Double-click **Schedule Backup.bat** (type 1) to make Windows do it every Sunday at 8 pm into `Documents\video-backups` (newest 8 kept; log in `out\backup.log`); run it again and type 2 to stop it. It stores no passwords or keys. `.env` and `secret.key` are never in a backup: keep your own copy of the Claude key in a password manager.
- **Same versions on a new computer:** `pip install -r requirements.lock.txt` then `pip install --no-deps -r requirements-nodeps.txt`.
- **Teach it words:** `prompts/vocabulary.txt` (towns and terms for the speech-to-text). `prompts/examples.md` is her real writing for the AI.

## When something breaks
| Symptom | Likely cause and fix |
|---|---|
| the broker says the link doesn't work | The window was closed, the lid was shut, the computer slept or restarted, or the tunnel restarted after a Wi-Fi drop: the link changed. Look in `current-link.txt` (run **Start Website.bat** if the window is gone) and send the new one. |
| It worked, then it stopped | `out\server.log` has the website's messages with a time for every restart. Many restarts in a row means a real bug: run `check_setup.py`, then read the last lines of the log. |
| Uploads fail | Check `check_setup.py`. Uploads go up in small pieces and resume by themselves; a real failure shows a plain message. |
| "The AI title writer wasn't available" note on videos | Key problem or no credit. `check_setup.py --api`. Videos still come out with simpler titles. |
| A video "didn't finish" | `out/failures.jsonl` has the technical detail. The page offers *Make it again*. |
| Everyone is signed out | Normal after a password change, or if `secret.key` was replaced. |
| Slow or stuck first video on a new computer | The speech model (about 500 MB) downloads once. Make one test video first. |

## A permanent link (and always-on hosting)
- A free tunnel's address changes each time. A **permanent address** needs a domain (about $10 a year) and a free Cloudflare account
  (a "named tunnel"), or move to a small always-on cloud server (the `Dockerfile`: **never built or run yet**; build it once and run
  `check_setup.py` inside it).
- Cloudflare's free plan limits a single upload to 100 MB. The page uploads in 8 MB pieces, so this should be fine: prove it with step 3 of the start-of-day checklist.
- Autostart (`Install Autostart.bat`) starts the website after you sign in to Windows, so it needs automatic sign-in, and still gives a new link each time.
- The Docker image also needs a disk mounted at `/data` that user 1000 can write to, and a one-time build and test (see the Dockerfile header).

## Open decisions (written down so they are not forgotten)
1. **Brokerage name.** the broker says disclosures are for print. The Iowa advertising rule appears to require the brokerage name on advertising
   including social media. Ask the brokerage's compliance for the exact line, then put it in `prompts/disclosure.txt` (it is then added to descriptions).
2. **Video length (cap 60 s or 3 min).** The tool makes videos under 60 seconds. YouTube now counts square or vertical videos up to 3 minutes as Shorts. Raising
   `MAX_SECONDS` in `video_tool/config.py` changes every video; TikTok and Reels limits differ.
3. **Always-on host, who pays and who supports it** after the class (the laptop must stay on and the free link changes on every restart: a cloud server, a named tunnel with a domain, or laptop-only; who answers the broker's texts and does the monthly check). Ask the broker whether she wants this kept running, and what it is worth to her.
4. **Photos to the AI.** Still frames of her clips go to Anthropic to line clips up with her words (no file names). Turn off with the
   *Don't share any pictures from my clips* option (it also sets `privacy: private` on the video), or `match_clips` in `video_tool/settings.py`, if a seller doesn't want that.
5. **Licences.** The Windows ffmpeg "full" build is GPL, DeepFilterNet and the speech models have their own terms. Check before sharing an
   installer or Docker image with anyone.
6. **Clip moving** (`auto_order`). Keep it on (the tool moves a clip to where she talks about it, only when the whole plan grades at least 5% better) or turn it off so her order is never changed.

## Features added since the first version (where to look)

| Feature | Where | Notes |
|---|---|---|
| Speech check ("did you forget the voice-over?") | `video_tool/own_audio.py` | Almost no speech in a talking upload stops with a plain message. |
| Retake cutting | `own_audio.find_retakes`, setting `cut_retakes` | Keeps the last take. Listed in `report.json` `retakes`. |
| Opening check | `own_audio.find_opening`, setting `start_at_hook` | Always reported (`opening`), cut only when asked. |
| Said but not shown | `pipeline.not_shown_report`, `match.not_shown` | Needs the matching scores already saved: no extra AI call. |
| Uncertain words | `transcribe.uncertain_words` | `report.json` `uncertain_words`; names the broker typed in "Names or places" (setting `hints`) and `prompts/vocabulary.txt` help the spelling. |
| Formats | `video_tool/formats.py`, setting `format` | house tour, recipe, tips, market update, Q and A. |
| Post pack | `video_tool/postpack.py`, `GET /api/jobs/{id}/postpack` | Zip with the video, captions.srt, cover.jpg, alt-text.txt and post-text.txt. |
| Ideas | `GET /api/ideas` | Claude when a key exists, a built-in seasonal list otherwise. |
| Posting log, week and reminder | `/api/jobs/{id}/posting`, `/api/posting/week`, `/api/posting/reminder.ics` | Her own record; nothing posts. |
| Example video | `GET /api/demo`, file `out/_demo.txt` | Holds the id of a finished video to show. Delete the file to hide the example. |
| Delete, change password, events | `DELETE /api/jobs/{id}`, `POST /api/change-password`, `POST /api/jobs/{id}/event` | Events go to `out/events.jsonl`. |
| Admin health and failures | `/api/admin/health`, `/api/admin/failures`, Team page | Disk, key present, last video, keep-awake, failed videos with detail. |
| Disclosure and Fair Housing checks | `writer.py` (code, not left to the AI) | Disclosure is added once at the end of each caption only when `prompts/disclosure.txt` is filled in. |
| Phone-app files | `web/manifest.webmanifest`, `web/sw.js`, `web/assets/` | Add to Home Screen. |
| Long video mode (Shorts from a long video) | `video_tool/highlights.py`, `video_tool/longform.py`, `pipeline.long_video`, setting `highlights`, `GET /api/jobs/{id}/longform`, `/long.srt` | One long talking video in; up to 5 Shorts and a YouTube post (title ideas, description with chapters, tags, `long.srt`) out. See the section above. |
| Restart loops, backups, checks | `Start Website.bat`, `Schedule Backup.bat`, `check_setup.py` | See the tables above. |

Settings keys are explained in REFERENCE.md ("Settings reference"), `report.json` and `copy.json` fields and the endpoint list too.

## What was tested and what was not

Long video mode: the three unit test scripts above, the browser part `longvideo`, and real runs on the 150-second Home Value sample with the key blank. **Not tested:** Claude's picks and the Claude YouTube post on her real videos (she approved Claude on 19 Oct 2026, but the Claude path has so far only been tried with fake clients and invented text, not on her real video), her actual old videos, a real phone uploading a long video, and videos over about 30 minutes.

Tested: all unit test scripts (`tests/test_*.py`, see REFERENCE.md "Tests") and the browser parts in a phone-sized Chromium; real videos from the recipe, tour and talking-tips samples; chunked upload with a browser-recorded voice-over (Chrome's fake microphone); the post pack; delete; password change.

**Not tested:** iPhone Safari and Android Chrome on real phones (including recording a voice-over with MediaRecorder on iPhone Safari, Save to my phone and Add to Home Screen); the real 150 MB upload through the tunnel from a phone on cellular; a Mac; a computer that has never had Python or ffmpeg; the Docker image (never built); the YouTube report against real Google data. Write the result here when you do the first three: `____`.

For showing the product to someone, use DEMO-DAY.md.
