# YouTube report: one-time setup

The report (`video_tool/yt_report.py`) reads the broker's channel numbers: videos per week, views, watch time,
Shorts vs long-form, and before vs with the tool. It is **read-only**. It cannot post, edit or delete.

Nothing here has been run against Google yet. The parsing is tested against stand-in data
(`tests/test_yt_report.py`); the first real run may need small fixes.

See the layout now, with made-up numbers:

```
venv\Scripts\python -m video_tool.yt_report --demo
```

Then open `out\youtube_report\demo.html`.

## Who does what

| Step | Who | Needs a login? |
|---|---|---|
| 1. Install the Google libraries | the maintainer | No |
| 2. Make a Google Cloud project and download `client_secret.json` | the maintainer | Yes: the maintainer's own Google account |
| 3. Add the broker as a test user | the maintainer | Same |
| 4. Approve read-only access to the channel | **the broker** | Yes: the Google account that owns her channel |
| 5. Run the report | the maintainer | No |

## 1. Install the libraries (the maintainer)

```
venv\Scripts\pip install google-api-python-client google-auth-oauthlib
```

## 2. Google Cloud project (the maintainer, about 10 minutes)

1. Go to https://console.cloud.google.com and sign in with **your** Google account.
2. Top bar: project picker > **New project**. Name it `the broker video report`. Create, then select it.
3. **APIs & Services > Library**. Search for and **Enable** both:
   - YouTube Data API v3
   - YouTube Analytics API
4. **APIs & Services > OAuth consent screen** (also called "Google Auth Platform"). Choose **External**.
   App name `the broker video report`, your email for support and developer contact. Save.
   Leave it in **Testing**. Do not publish it.
5. On the same screen, under **Test users** (or "Audience"), add the Google email that owns the broker's
   YouTube channel, and your own.
6. **APIs & Services > Credentials > Create credentials > OAuth client ID**.
   Application type: **Desktop app**. Create.
7. Click **Download JSON** right away. Google only shows the secret at creation.
8. Make a folder named `yt` inside `video-maker`, put the file there, and rename it
   `client_secret.json`. The `yt/` folder is gitignored. Never share or commit this file.

## 3. the broker approves access (the broker, about 2 minutes)

The approval has to happen in a browser on the computer that runs the report, signed in as the
channel owner. Two ways:

- **Together:** at your meeting, run the command in step 4 on your laptop and hand it to her.
  She signs in to Google herself and clicks **Allow**. You never see or type her password.
- **Add you as a channel manager instead:** in YouTube Studio > Settings > Permissions she can invite
  your Google account as a **Viewer**. Then you approve with your own account. Check that the
  report still shows her channel; if it shows yours, use the first way.

She will see a "Google hasn't verified this app" warning. That is expected for an app in Testing:
**Advanced > Go to the broker video report**. The permissions listed should be only "View your YouTube
account" and "View YouTube Analytics reports".

While the app is in Testing the approval lasts **7 days**. After that the command opens the browser
again and she clicks Allow again.

## 4. Run it (the maintainer)

```
venv\Scripts\python -m video_tool.yt_report --tool-start 2026-10-06 --days 120
```

- `--tool-start`: the date she first posted a video made with the tool. Everything before it is "before".
- `--days`: how far back to look.

Output: `out\youtube_report\report.html` and `report_videos.csv`.

## Reading it

- YouTube's numbers run 2 to 3 days behind.
- "Views" counts every play since YouTube's 2025 change. The CSV also has `engagedViews`, the older
  stricter count: use that when comparing across the change.
- "Short?" with a question mark means the type was guessed from the video's length.
- "Tool title: edited" is a rough text match. Check those by hand before quoting a count.
- Editing time per video is not on YouTube. That comes from the timing you collect from her.

## To undo

the broker can remove access any time at https://myaccount.google.com/permissions. Deleting
`yt\token.json` also forgets the approval on this computer.
