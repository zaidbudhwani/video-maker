"""Add or change website accounts.   python manage_users.py add alex   |  passwd NAME  |  remove NAME  |  list

Each person gets their own login, saved caption/pause/music choices, and videos. Add --admin to see
everyone's feedback on the /admin page.
"""
import getpass
import sys

from web import auth

cmd, *rest = sys.argv[1:] or ["list"]
if cmd == "list":
    for n, u in auth.load_users().items():
        print(n, "(admin)" if u.get("admin") else "")
elif cmd in ("add", "passwd") and rest:
    pw = getpass.getpass("Password (6+ characters): ")
    print("OK:", auth.set_user(rest[0], pw, admin=True if "--admin" in rest else None))
elif cmd == "remove" and rest:
    auth.remove_user(rest[0].lower())
else:
    print(__doc__)
