# Installing the Video Maker on another computer

For putting the tool on the broker's own computer, or on a spare one that stays switched on.
Plan on 30 to 60 minutes, mostly waiting for downloads. You need to be at the computer.

## What the computer needs

- **Windows 10 or 11.** It is not set up for Mac.
- **8 GB of memory or more.** Less works for short videos but is slow, and long talking videos may fail.
- **About 10 GB of free disk** to start. Every video keeps its working files, so more is better.
- **Internet** for the install (about 1.5 GB of downloads), for the AI titles, and for using it from a phone.

An older computer works. It is just slower: a video that takes 2 minutes on a newer laptop may take 5 to 10.

## The quick way: one file

Send her **Get Video Maker.bat** (email it, or put it in the shared Drive). She saves it to her Desktop and
double-clicks it. It does everything on this page:

1. Installs Git and the GitHub tool.
2. Signs her in to GitHub in her own browser.
3. Downloads the project to a `VideoMaker` folder in her user folder.
4. Runs the installer and puts **Video Maker** on her Desktop.

Double-clicking it again later updates to the latest version.

**Before she runs it,** her GitHub account has to be invited to the private repository (the maintainer: the
repository's Settings > Collaborators page, or ask Claude to send the invitation). She accepts the
invitation email first. A free GitHub account is enough.

She still does three things herself, because they are hers: sign in to GitHub, paste her Claude API key
into the settings file when Notepad opens, and choose her website password.

The rest of this page is what that file does, for doing it by hand or fixing a step.

## 1. Get the project onto the computer

**From GitHub (private repository):**

1. Install Git if it isn't there: open PowerShell and run `winget install Git.Git`. Close and reopen PowerShell.
2. Run `git clone https://github.com/YOUR-NAME/video-maker.git` (use the real address).
   It will ask you to sign in to GitHub, because the repository is private.

**Or with a USB stick:** copy the whole `video-maker` folder, but leave out `venv`, `out`, `samples`,
`.env`, `users.json`, `secret.key` and `users`. Those are this computer's own.

Put the folder somewhere simple with a short path, such as `C:\VideoMaker`. Avoid OneDrive folders.

## 2. Run the installer

Double-click **Install Video Maker.bat**.

- If Windows shows a blue "Windows protected your PC" box: **More info**, then **Run anyway**.
- If it asks to allow changes (installing Python or ffmpeg): **Yes**.

It does eight things and says which one it is on:

1. Checks the computer (Windows version, memory, disk).
2. Installs Python 3.11.
3. Installs ffmpeg (the video engine) and cloudflared (the shareable link).
4. Installs the Python packages at the exact versions that were tested.
5. Downloads the face-detection model and the noise remover.
6. Downloads the speech-to-text model.
7. Makes the settings file and your sign-in. Notepad opens for two lines (see below); then it asks
   for a sign-in name and password.
8. Runs the checker and lists anything still missing.

It is safe to run again. It skips what is already there and never overwrites settings, accounts or videos.

### The two lines in Notepad

- `ANTHROPIC_API_KEY=` her own key from https://console.anthropic.com (API Keys > Create Key, with a workspace
  chosen). This pays for the AI titles and clip matching: roughly 5 to 10 cents a video.
  **A Claude or ChatGPT subscription does not include this.** The API is billed separately, pay as you go.
  With no key the tool still makes videos, with plainer titles and no clip matching.
- `HELP_CONTACT=` who she contacts when stuck, for example `Text the maintainer at 319-555-0100`.

Save and close Notepad to continue.

## 3. Start it

| How she will use it | Double-click | Then |
|---|---|---|
| On this computer only | **Start Video Maker.bat** | A browser opens. No link to share. |
| From her phone, or anywhere | **Start Website.bat** | Use the `.trycloudflare.com` link it shows. Keep the window open. |

For a spare computer that stays on, also double-click **Install Autostart.bat** once, so the website starts
by itself when Windows starts. Turn off sleep: Settings > System > Power > Screen and sleep > When plugged in,
put my device to sleep after > **Never**.

The free link changes each time the website restarts. A permanent address needs a free Cloudflare account and
a domain (about $10 a year): see HANDOVER.md.

## 4. Try it

Make one short video with two or three clips. If it works, she is set.

## If something goes wrong

- Run the checker any time: `venv\Scripts\python check_setup.py` (add `--api` to test the key).
  It says what is fine, what is missing, and the one line that fixes it. It also warns if closing the laptop lid would stop the
  website, if the "need help?" line (`HELP_CONTACT`) is empty, if her website is not yet in `prompts/contact.txt`, and if the disk is nearly full.
- The installer stopped partway: fix what it named and double-click it again.
- "winget is not recognized": open the Microsoft Store, install **App Installer**, run it again.
- Videos fail on a clip: the page says which file. Technical detail is in `out\failures.jsonl`.

## Getting updates later

For a weekly backup, double-click **Schedule Backup.bat** and type 1 (see HANDOVER.md). **Start Website.bat** now starts the website and the tunnel again by itself if either stops.

Double-click **Update Video Maker.bat** in the folder, or **Get Video Maker.bat** again. It gets the latest
version and re-checks the install. Her settings, accounts and videos are kept.

## What has and hasn't been tested

Tested: **Get Video Maker.bat** run from nothing on the laptop it was built on. It downloaded the project
from GitHub, installed packages, models and settings, and passed the checker; a second run updated cleanly;
and a freshly installed copy made a real video. The GitHub sign-in step itself was not exercised, because
that laptop was already signed in. **Not tested:** a computer that has never had Python or ffmpeg, an
older or slower computer, or any computer other than the maintainer's. Do the first real install on a teammate's
Windows laptop before the broker's.

Also **not tested** on any computer but the maintainer's: the Docker image (never built), iPhone and Android browsers (including recording a
voice-over in the browser on an iPhone), a Mac, and the real 150 MB upload from a phone through the tunnel. See REFERENCE.md "What was tested, and what was not".
