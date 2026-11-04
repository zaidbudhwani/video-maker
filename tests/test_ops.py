"""Ops lane unit tests: check_setup helpers, tunnel exit codes, .bat/Docker/ignore file contents.  venv/Scripts/python tests/test_ops.py"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check_setup as cs  # noqa: E402
import tunnel  # noqa: E402

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# power_values
sample = "Possible Setting Index: 0x00000000\n Current AC Power Setting Index: 0x00000000\n Current DC Power Setting Index: 0x00000001\n"
check("power_values lid nothing/sleep", cs.power_values(sample) == (0, 1))
check("power_values seconds", cs.power_values("Maximum: 0xffffffff\n AC: 0x00000258\n DC: 0x0000012c") == (600, 300))
check("power_values none", cs.power_values("Invalid Parameters") is None and cs.power_values("") is None and cs.power_values(None) is None)
check("power_values one number is not enough", cs.power_values("0x00000001") is None)

# env_key_state: never returns the key
d = Path(tempfile.mkdtemp())
check("env missing file", cs.env_key_state(d / "nope") == "nofile")
(d / "a").write_text("FOO=1\nANTHROPIC_API_KEY=\n", encoding="utf-8")
check("env empty key", cs.env_key_state(d / "a") == "nokey")
(d / "b").write_text("ANTHROPIC_API_KEY=your_key_here\n", encoding="utf-8")
check("env placeholder key", cs.env_key_state(d / "b") == "nokey")
(d / "c").write_text("# ANTHROPIC_API_KEY=abc\n", encoding="utf-8")
check("env commented key", cs.env_key_state(d / "c") == "nokey")
(d / "d").write_text("X=1\r\nANTHROPIC_API_KEY = \"sk-test-123\"\r\n", encoding="utf-8")
check("env set key (spaces, quotes, CRLF)", cs.env_key_state(d / "d") == "set")
check("env state is a plain word", cs.env_key_state(d / "d") in ("set", "nokey", "nofile"))

# contact website
f = cs.contact_website_filled
check("contact shipped file (commented website) is not filled", not f("# the maintainer: keep as \"# website: www.example.com\"\nphone: 555-010-0123\n# website:\n"))
check("contact real website", f("phone: 1\nwebsite: www.BrokerHomes.com\n"))
check("contact Website https", f("Website: https://brokerhomes.com"))
check("contact empty value", not f("website:\n"))
check("contact placeholder", not f("website: www.example.com") and not f("website: your website here"))
check("contact empty/None", not f("") and not f(None))
check("contact bare url in a comment ignored", not f("# see www.brokerhomes.com\n"))

# tunnel: "not installed" is 42, and the lid/real exit code never is
tunnel.tunnel_command = lambda: None
check("tunnel not installed -> 42", tunnel.main() == 42)

# the files
bat = (ROOT / "Start Website.bat").read_text(encoding="utf-8")
check("start bat loops tunnel", ":tunnelloop" in bat and "goto tunnelloop" in bat)
check("start bat loops server and logs", ":serverrun" in bat and "goto serverrun" in bat and "out\\server.log" in bat)
# a busy port must never turn into a restart loop that stops videos (found when a second server held port 8000)
serverrun = bat[bat.index(":serverrun" + chr(10)) if (":serverrun" + chr(10)) in bat else bat.index(":serverrun" + chr(13)):bat.rindex(":killvideos")]
check("start bat waits instead of restarting when port 8000 is taken", "TcpClient" in serverrun and "timeout /t 30" in serverrun)
check("start bat only stops leftover videos after that check", serverrun.index("TcpClient") < serverrun.index("call :killvideos"))
check("start bat stops leftover pipeline", "video_tool.pipeline" in bat)
check("start bat stops on missing tunnel program", "errorlevel 42" in bat)
check("start bat has no raw secrets", ".env" not in bat.replace("findstr", "") or "APP_PASSWORD=..*" in bat)
sb = (ROOT / "Schedule Backup.bat").read_text(encoding="utf-8")
check("schedule backup: weekly + remove + maintain backup", "/SC WEEKLY" in sb and "schtasks /Delete" in sb and "maintain.py backup" in sb)
code = " ".join(l for l in sb.splitlines() if not l.lower().startswith("rem "))
check("schedule backup stores no secrets", not re.search(r"ANTHROPIC|sk-|secret[.]key|[.]env|APP_PASSWORD", code, re.I))
df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
check("docker uses lock then nodeps", df.index("requirements.lock.txt") < df.index("requirements-nodeps.txt") and "--no-deps -r requirements-nodeps.txt" in df)
check("docker non-root + fonts", re.search(r"^USER (?!root)\w+", df, re.M) is not None and "fonts-liberation" in df)
di = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
for need in ("users/", "assets/music/", ".env", "out/", "samples/", "secret.key", "venv/"):
    check(f"dockerignore has {need}", need in di)
gi = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
check("gitignore has current-link.txt", "current-link.txt" in gi)

print("\nFAILED: " + ", ".join(fails) if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
