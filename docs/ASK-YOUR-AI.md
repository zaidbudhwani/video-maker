# Ask your AI to install or explain it

Copy one of these into Claude, ChatGPT, Codex or any assistant, **together with this folder** (or the repository link, if it
can open it). Nothing else is needed: the assistant reads `AGENTS.md` and `docs/ARCHITECTURE.md` itself.

## If the assistant can run commands on your computer (Claude Code, Codex CLI, a computer-use agent)

> This folder is a Windows video tool. Read `AGENTS.md` first, then install it for me: run the installer, run
> `check_setup.py`, and make one real test video to prove it works. Do the steps yourself and only stop for the three things
> only I can do (sign in to GitHub, paste my own Claude key into `.env`, choose my website password). Never print my `.env`
> and never ask me to paste a key into the chat. When it is done, tell me in plain words how to start it and how to update it.

## If it is a plain chat assistant (cannot touch your computer)

> I want to install the video tool in this folder on my Windows computer. I am not technical. Read `AGENTS.md`, then walk me
> through the install one small step at a time. After each step wait for me to tell you what I saw. Never ask me to paste a
> password or an API key into this chat: when a key is needed, tell me which file to open and which line to change.

## If you only want it explained

> Read `docs/ARCHITECTURE.md`, then explain this project to me: what it does, how a video gets made from the clips to the
> finished file, what goes to an outside AI service and what never does, and what is still untested. Then answer my questions.

## If you want it changed

> Read `AGENTS.md` and `docs/ARCHITECTURE.md`. I want this change: <what you want>. Make it, run the tests the way
> `AGENTS.md` says (`tests/run_all.py` for unit tests; run the browser tests alone, never at the same time as another run),
> and tell me what you changed and what you could not test. Keep her raw style and the rules at the bottom of `AGENTS.md`.

## Notes

- A Claude or ChatGPT **subscription** does not include the API key the tool uses for titles and clip matching. That key comes
  from console.anthropic.com and is billed per use (about 5 to 10 cents a video). Without it the tool still works.
- The repository is private: the AI needs access (a local copy of the folder is the simplest way), and the client's writing in
  `prompts/examples.md` should not go to a service she has not agreed to. If in doubt, leave that file out of what you share.
