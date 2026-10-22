# Video Maker: instructions for AI assistants (Claude, ChatGPT, Codex, any other)

This repository turns a real estate broker's phone clips into captioned vertical short videos. You may be running on the
developer's computer or on the user's own computer helping them install or fix it. If the person is not a developer, use plain
words and do the steps for them where you can.

## Read this first

1. **What this is and how it works:** `docs/ARCHITECTURE.md` (a 10-minute read: the pipeline, where every file lives, what is
   stored where, how to test a change). Read it before changing code or explaining the project.
2. **What you can and cannot do:** if you are a chat assistant with no access to the computer, give the person the exact steps
   below one at a time in plain words and ask what each one printed. If you are an agent with a shell, run the steps yourself
   and hand over only the things listed under "What only the person can do".
3. **Rules that never bend** are at the bottom ("If you change code").

## Installing on a new Windows computer

Everything is scripted. Do not install pieces by hand unless a script fails.

1. In the project folder run: `powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1`. Add `-Unattended` if you are
   running it yourself: it then asks no questions and opens no windows. It is safe to run again and never overwrites `.env`,
   accounts or videos.
2. Check the result: `venv\Scripts\python check_setup.py` (add `--api` to test the Claude key with one tiny call). Every line says
   what is fine, what is missing, and the one command that fixes it. Fix the FAIL lines.
3. Prove it works by making a real video: `venv\Scripts\python -m video_tool.pipeline <a folder with one or two clips> --job-id install-proof`
   then confirm `out\install-proof\final.mp4` exists.

### What only the person can do (never do these for them)

- **Put the Claude API key in `.env`** (optional). Open the file in Notepad for them (`notepad .env`) and tell them which line.
  Never ask them to paste a key into the chat, never read `.env` back, never print its contents. To check a key is set without
  seeing it, use `check_setup.py --api`.
- **Choose their website password.** Run `venv\Scripts\python manage_users.py add NAME --admin`; it asks with hidden typing.
- **Sign in to any third-party service** in their own browser.

Without a key the tool still makes videos, with plainer titles and no clip matching. An API key comes from console.anthropic.com
and is billed per use, roughly 5 to 10 cents a video.

## Starting it

- `Start Video Maker.bat`: this computer only, opens http://localhost:8000.
- `Start Website.bat`: also gives a public link for a phone; it restarts the website and tunnel after a crash.
- `Schedule Backup.bat`, `Install Autostart.bat`, `Update Video Maker.bat`: weekly backup, start with Windows, update.

## When something is wrong

1. `venv\Scripts\python check_setup.py` first.
2. A video failed: the website shows a plain message; the technical detail is the last line of `out\failures.jsonl` and the full
   log is `out\<job id>.log`.

Known sharp edges:
- Install packages only with `pip install --no-deps -r requirements.lock.txt` then `pip install --no-deps -r requirements-nodeps.txt`.
  Letting pip resolve dependencies pulls in `opencv-python`, which breaks the `opencv-contrib-python` that mediapipe needs.
- Do not `pip install deepfilternet`: it downgrades numpy. Noise removal uses `assets\tools\deep-filter.exe`.
- ffmpeg from winget is not on PATH until a new window opens; `video_tool/config.py` finds it anyway.
- Windows only.

## If you change code

- Test for real before saying it works. Unit tests: `venv\Scripts\python tests\run_all.py` (plain scripts, PASS/FAIL lines, no
  AI calls). Browser tests: `venv\Scripts\python tests\e2e_site.py` (starts its own copy of the site with throwaway data;
  `E2E_PORT` picks the port; `E2E_ONLY=<part>` runs one `tests/e2e_part_<part>.py`; `--ai` uses the real key and costs a few cents,
  so never run it unless asked). Test your own changes on your own port.
- Settings, `report.json` and `copy.json` fields and the website endpoints are listed in `docs/REFERENCE.md`. Update them when you change them.
- **Long video mode** (setting `highlights` 1 to 5, one long talking video): only transcript text may go to Claude in this mode,
  never a picture, a video piece or a file name. Every AI step has a no-AI fallback.
- **Outside services:** Claude is the only outside AI, and only when a key is set. Do not add another provider without the
  owner's say-so. Test AI paths with fake clients where you can.
- Everything the end user sees is plain English: no file paths, no code words, no blame.
- Copy about a property describes the place, never who it suits (Fair Housing: no "family-friendly", "safe area", "perfect for").
- Nothing posts automatically and the tool never emails or texts anyone. No posting APIs, no social logins.
- Never add AI-generated footage, voice or images.
- The style is raw: no transitions, zooms or effects. Problems should be fixed where possible and otherwise shown as a plain
  warning; a warning must never stop a video.
- Never commit `.env`, `users.json`, `secret.key`, `yt/`, videos or anything in `out/`. Keep real client writing and contact
  details out of the repository: `prompts/examples.md` and `prompts/contact.txt` are templates.
- Every password field gets the hold-to-peek eye button (see `web/login.html`).
