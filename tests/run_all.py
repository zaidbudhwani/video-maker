r"""Run every unit test suite (tests/test_*.py) and print one summary. No AI calls, no network.

    venv\Scripts\python tests\run_all.py            (add -v to see each suite's output, or name suites: run_all.py engine content)

Exit code 0 when nothing failed. A suite that needs the client's footage or a saved job (kept off this repository on
purpose) prints a line starting with SKIP and counts as skipped, not failed. The browser tests are separate
(tests\e2e_site.py, 12 to 15 minutes, run it alone).
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main(argv):
    verbose = "-v" in argv
    names = [a for a in argv if not a.startswith("-")]
    suites = sorted(HERE.glob("test_*.py"))
    if names:
        suites = [s for s in suites if any(n in s.stem for n in names)]
    if not suites:
        print("No test suites matched.")
        return 2
    rows, failed = [], []
    t_all = time.time()
    for s in suites:
        t0 = time.time()
        p = subprocess.run([sys.executable, str(s)], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        lines = [l for l in out.splitlines() if l.strip()]
        last = lines[-1] if lines else ""
        if p.returncode == 0 and last.upper().startswith("SKIP"):
            state = "SKIP"
        elif p.returncode == 0:
            state = "PASS"
        else:
            state = "FAIL"
            failed.append(s.name)
        rows.append((state, s.name, time.time() - t0, last))
        print(f"{state:5s} {s.name:34s} {time.time() - t0:6.1f}s  {last[:70] if state != 'PASS' else ''}", flush=True)
        if verbose or state == "FAIL":
            print("\n".join("      " + l for l in lines[-25:]), flush=True)
    n = {k: sum(1 for r in rows if r[0] == k) for k in ("PASS", "SKIP", "FAIL")}
    print(f"\n{n['PASS']} passed, {n['SKIP']} skipped, {n['FAIL']} failed in {time.time() - t_all:.0f}s")
    if failed:
        print("Failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
