"""Runs the Cloudflare tunnel and catches the new address, so there is no hunting through the window for it.

When the link appears it is shown in a big banner, saved to current-link.txt and copied to the clipboard.
(A free tunnel gets a NEW address every time it starts, also when Start Website.bat restarts it after a Wi-Fi drop.
A permanent one needs a domain: see docs/HANDOVER.md.)
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LINK = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def tunnel_command():
    """The cloudflared program, found where winget puts it or on the PATH. ALEX_TUNNEL_CMD overrides it (used in tests)."""
    if os.getenv("ALEX_TUNNEL_CMD"):
        return os.environ["ALEX_TUNNEL_CMD"].split("|")
    for guess in (r"C:\Program Files (x86)\cloudflared\cloudflared.exe", r"C:\Program Files\cloudflared\cloudflared.exe"):
        if Path(guess).exists():
            return [guess, "tunnel", "--no-autoupdate", "--protocol", "http2", "--url", "http://localhost:8000"]
    found = shutil.which("cloudflared")
    if found:
        return [found, "tunnel", "--no-autoupdate", "--protocol", "http2", "--url", "http://localhost:8000"]
    return None


def announce(url):
    (ROOT / "current-link.txt").write_text(url + "\n", encoding="utf-8")
    copied = ""
    try:
        subprocess.run(["clip"], input=url.encode("ascii"), check=True, timeout=5)
        copied = "  (copied: just paste it into a text)"
    except Exception:
        pass
    bar = "=" * 64
    print(f"\n{bar}\n   ALEX'S LINK:  {url}{copied}\n   Also saved in current-link.txt\n   If the connection drops it is made again with a new link.\n{bar}\n", flush=True)


def main():
    cmd = tunnel_command()
    if not cmd:
        print("The tunnel program isn't installed. Install it once with:  winget install Cloudflare.cloudflared", file=sys.stderr)
        return 42   # "not installed": Start Website.bat stops here instead of trying again forever
    seen = False
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    try:
        for line in proc.stdout:
            if not seen:
                m = LINK.search(line)
                if m:
                    seen = True
                    announce(m.group(0))
            elif "ERR" in line or "error" in line.lower():
                print(line.rstrip(), flush=True)   # connection trouble is worth seeing; the routine chatter is not
    except KeyboardInterrupt:
        proc.terminate()
    return 1 if proc.wait() else 0   # never 42 by accident


if __name__ == "__main__":
    sys.exit(main())
