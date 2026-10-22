# Background music

the broker wants quiet background music she can use on every platform without copyright claims (her answer, 19 Oct 2026).
The tool already adds a flat music bed under the voice (`video_tool/music.py`, about -22 dB, a 1.5 second fade at the end;
the first track in `assets/music/` by file name is used). **Music is off in a video that has no track in that folder.**

## What is installed

Four instrumental tracks from Pixabay, listed with their page links in `assets/music/LICENSES.txt`. They are under the
Pixabay Content License (free, no attribution, fine in videos on any platform; it is not a Creative Commons licence).
The folder is deliberately not in git, so **copy the tracks to any other computer by hand** (her Windows laptop).

## The rule for adding more

1. Use only tracks marked **free to use** with no "attribution required" line: Pixabay Music or the YouTube Audio Library.
   Never use a song from the radio, a streaming service or a video you did not make.
2. On a Pixabay track page, open the page source or look for "Content ID Registered". Do not use a track that says **true**:
   YouTube may then claim videos that use it, which is exactly what she wants to avoid. The page data shows it
   (`additionalProperty` named "Content ID Registered"). Prefer tracks that have been on the site for a while: an author can
   register a track later, so a brand-new track carries more risk.
3. Download the MP3 into `assets/music/`, then add a line to `assets/music/LICENSES.txt` with the title, author, page link and
   the date you checked.
4. If YouTube ever shows a claim on a video, remove that track from the folder, and tell the team which video it was.

## What to say to her

The tracks are free for her videos and need no credit. A free licence does not make a claim impossible: if a claim ever
appears, she can dispute it in YouTube Studio (name the Pixabay licence) and we replace the track.
