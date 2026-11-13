# Video Maker

**Turn raw phone clips into captioned, ready-to-post short videos, with a person reviewing every one.**

Video Maker takes a folder of phone clips (and an optional voice-over) and builds a finished vertical video: clips matched to
the words, burned-in captions, three title ideas and a caption for each of YouTube Shorts, TikTok, Instagram Reels and
Facebook Reels. A small website lets the person upload from a phone, review, ask for a small change in plain words, and save
the result. Three kinds of video:

- **Short vertical video** (under 60 s) for TikTok, Instagram Reels, Facebook Reels and YouTube Shorts, with a caption for each.
- **Horizontal YouTube video** (1920x1080, up to 15 minutes) with titles, a description and captions.
- **Shorts from a long video:** 1 to 5 vertical Shorts cut from one long talking video, plus its YouTube title, description and chapters.

Nothing posts automatically. The tool never emails or texts anyone. Style stays raw: her footage, her voice, plain captions,
natural cuts, no effects, transitions, zooms or generated footage.

**Windows 10/11.** Python 3.11, ffmpeg, a small FastAPI website, Whisper for speech, and Claude for the writing (optional).

## What it does

| | |
|---|---|
| **Upload from a phone** | Pick clips and a voice-over; large files upload in resumable chunks. |
| **Captions and cuts** | Speech to text, natural pause trimming, fillers removed, loudness set to a normal level. |
| **Matches clips to words** | Claude looks at small stills and picks the clip that fits each phrase. Without a key, clips keep their filmed order. |
| **Your words and your clips** | A list of every phrase with its clip. Tap one to jump there, or **Change this shot** to pick another clip. |
| **Ask for changes in plain words** | "Make the captions bigger." A plain preview says what will change, then you confirm. Earlier versions are kept, and you can compare two side by side. |
| **Finished-video checks** | Warns about black frames, a frozen ending, repeated footage, captions off the frame, sound drift. |
| **Vertical and horizontal** | 1080x1920 Shorts, or a 1920x1080 YouTube video. |
| **Music** | Choose a track with a 10-second preview, or none. Optional gentle ducking under speech. |
| **Shot list** | Paste what you plan to say and get the shots to film, with simple filming tips. |
| **Long video mode** | One long talking video becomes 1 to 5 Shorts plus a YouTube post with chapters. |
| **Team page** | Counts videos, remakes, timed reviews and time saved. |

Every AI step has a plain no-AI fallback, so the core workflow works with no key. A warning never stops a video.

## Install it

Double-click `Install Video Maker.bat`. It installs Python, ffmpeg and the packages, checks itself, and puts a
**Video Maker** icon on the Desktop. It is safe to run again and never overwrites `.env`, accounts or videos.
To install by hand or with an AI assistant, see [`docs/INSTALL.md`](docs/INSTALL.md) and [`AGENTS.md`](AGENTS.md).

Three things only you can do: put your own Claude API key in `.env` (optional; billed per use, a few cents a video), choose
a website password, and sign in to the services you use.

## Use it

| Double-click | What it does |
|---|---|
| `Start Video Maker.bat` | Runs on this computer only and opens the browser. |
| `Start Website.bat` | Also makes a public link for a phone. Restarts itself if it drops. |
| `Update Video Maker.bat` | Gets the latest version. Settings, accounts and videos are kept. |

No website needed: `venv\Scripts\python -m video_tool.pipeline <folder of clips and a voice-over>` makes a video from a folder.

## Check it

```
venv\Scripts\python check_setup.py        # what is fine, what is missing, the one command that fixes it
venv\Scripts\python tests\run_all.py      # the unit tests (no AI calls)
venv\Scripts\python tests\e2e_site.py     # the whole website in a real browser (12 to 15 minutes, run alone)
```

Some tests need a few sample clips in `samples/` (not included). Those are skipped when the folder is missing.

## Where to read next

| You want to... | Read |
|---|---|
| Understand how it works and where things are | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Install on another computer | [`docs/INSTALL.md`](docs/INSTALL.md) |
| Settings, report fields, endpoints, long video mode | [`docs/REFERENCE.md`](docs/REFERENCE.md) |

## Rules the project keeps

Raw style: no transitions, zooms or effects. Nothing posts by itself. Footage and words go to no outside service except
Claude, and only when a key is set. Everything the user sees is plain English. Copy about a property describes the place,
never who it suits. Never commit `.env`, `users.json`, `secret.key`, `yt/`, videos or `out/`.

## Credits

Created by **Zaid Budhwani**. Questions or ideas? Open an issue.
