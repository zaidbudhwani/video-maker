"""Accounts for the website: each person has their own login, saved settings and videos.

users.json (in the data folder) holds {username: {salt, hash, admin}}. Passwords are scrypt-hashed.
A signed cookie keeps people logged in. Add people with manage_users.py, or from the admin page.
"""
import base64
import logging
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from video_tool.config import DATA_DIR

USERS_FILE = DATA_DIR / "users.json"
SECRET_FILE = DATA_DIR / "secret.key"
USER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")
COOKIE = "alex_session"
SESSION_DAYS = 90            # a sliding window: the site hands out a fresh cookie about once a day while she uses it
REFRESH_AFTER = 86400         # ...so she is only signed out after SESSION_DAYS of not visiting
LOCAL_USER = "local"          # no-login mode: Start Video Maker.bat on the owner's own computer

log = logging.getLogger("alex.auth")
_lock = threading.Lock()
_fails = {}                   # (ip, user) -> [count, locked_until]


def _hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


class UsersFileError(RuntimeError):
    """users.json exists but can't be read, and there is no usable backup. Never treated as 'no users'."""


def _read_users(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("users file is not a list of accounts")
    return data


def load_users():
    """{name: account}. No file yet = {}. A broken file falls back to users.json.bak (and is repaired from it);
    if that fails too this raises UsersFileError rather than pretending nobody has an account."""
    if not USERS_FILE.exists():
        return {}
    try:
        return _read_users(USERS_FILE)
    except (OSError, ValueError) as e:
        bak = USERS_FILE.with_name(USERS_FILE.name + ".bak")
        print(f"!!! users.json cannot be read ({e}). Trying the backup users.json.bak.", flush=True)
        try:
            data = _read_users(bak)
        except (OSError, ValueError) as e2:
            print(f"!!! The backup users.json.bak cannot be read either ({e2}). Accounts are unavailable until it is fixed.",
                  flush=True)
            raise UsersFileError("users.json is unreadable and there is no usable backup") from e2
        try:
            shutil.copyfile(bak, USERS_FILE)   # put the good copy back so the next read is clean
            print("!!! Restored users.json from users.json.bak.", flush=True)
        except OSError:
            pass
        return data


def _save(users):
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, indent=1), encoding="utf-8")
    if USERS_FILE.exists():
        try:
            _read_users(USERS_FILE)   # only a readable file is worth keeping as the backup
            shutil.copyfile(USERS_FILE, USERS_FILE.with_name(USERS_FILE.name + ".bak"))
        except (OSError, ValueError):
            pass
    os.replace(tmp, USERS_FILE)   # all-or-nothing: the old file stays until the new one is complete


def clean_name(name):
    name = (name or "").strip().lower()
    if name == LOCAL_USER:
        raise ValueError(f"'{LOCAL_USER}' is a reserved name. Pick another username.")
    if not USER_RE.match(name):
        raise ValueError("Username: 2-32 letters, numbers, dot, dash or underscore.")
    return name


def set_user(name, password, admin=None, min_len=8):
    name = clean_name(name)
    if len(password or "") < min_len:
        raise ValueError(f"Password must be at least {min_len} characters.")
    with _lock:
        users = load_users()
        salt = secrets.token_bytes(16)
        old = users.get(name, {})
        users[name] = {"salt": salt.hex(), "hash": _hash(password, salt).hex(),
                       "admin": old.get("admin", False) if admin is None else bool(admin)}
        _save(users)
    return name


def prune_fails(now=None):
    """Forget lock-outs that have run out and counters that are empty, so the table can't grow without end."""
    now = now or time.time()
    for k in [k for k, (c, until) in list(_fails.items()) if until <= now and c == 0]:
        _fails.pop(k, None)
    if len(_fails) > 5000:   # someone is spraying names: keep only the active lock-outs
        for k in [k for k, (c, until) in list(_fails.items()) if until <= now]:
            _fails.pop(k, None)


def remove_user(name):
    with _lock:
        users = load_users()
        users.pop(name, None)
        _save(users)


def check_login(ip, name, password):
    """True if the login is right. Five misses in a row lock that name+address out for a minute."""
    key = (ip, (name or "").strip().lower())
    now = time.time()
    prune_fails(now)
    count, until = _fails.get(key, [0, 0])
    anywhere = _fails.get(("*", key[1]), [0, 0])   # misses for this name from ANY address
    if until > now or anywhere[1] > now:
        return None
    try:
        u = load_users().get(key[1])
    except UsersFileError:
        return False   # already logged loudly; nobody can sign in until the file is fixed
    # Always hash, even for unknown names, so timing doesn't reveal which names exist
    salt = bytes.fromhex(u["salt"]) if u else b"\0" * 16
    ok = hmac.compare_digest(_hash(password or "", salt).hex(), u["hash"] if u else "x" * 64) and bool(u)
    if ok:
        _fails.pop(key, None)
        _fails.pop(("*", key[1]), None)
        return key[1]
    wide = _fails.setdefault(("*", key[1]), [0, 0])
    wide[0] += 1
    if wide[0] >= 20:
        wide[0], wide[1] = 0, now + 60
    count += 1
    _fails[key] = [count, now + 60 if count >= 5 else 0]
    if count >= 5:
        _fails[key][0] = 0
    return False


def _secret():
    if not SECRET_FILE.exists():
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECRET_FILE.write_text(secrets.token_hex(32), encoding="utf-8")
    return SECRET_FILE.read_text(encoding="utf-8").strip().encode()


def make_cookie(name):
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    try:
        tag = (load_users().get(name) or {}).get("hash", "")[:12]
    except UsersFileError:
        tag = ""
    payload = base64.urlsafe_b64encode(f"{name}|{exp}|{tag}".encode()).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def cookie_needs_refresh(value):
    """True when a valid cookie is more than a day old (its expiry is more than a day short of a fresh one)."""
    try:
        payload = (value or "").rsplit(".", 1)[0]
        exp = int(base64.urlsafe_b64decode(payload.encode()).decode().split("|")[1])
    except (ValueError, TypeError, IndexError):
        return False
    return exp - time.time() < SESSION_DAYS * 86400 - REFRESH_AFTER


def read_cookie(value):
    """The username in a valid, unexpired cookie whose account still exists, else None."""
    try:
        payload, sig = (value or "").rsplit(".", 1)
        good = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        name, exp, tag = base64.urlsafe_b64decode(payload.encode()).decode().split("|")
        user = load_users().get(name)
        if int(exp) < time.time() or not user or not hmac.compare_digest(tag, user["hash"][:12]):
            return None   # expired, deleted account, or the password was changed since this cookie was made
        return name
    except (ValueError, TypeError, UsersFileError):
        return None


def is_admin(name):
    if name == LOCAL_USER:
        return True
    try:
        return bool(load_users().get(name, {}).get("admin"))
    except UsersFileError:
        return False


def bootstrap():
    """Make the first accounts on a fresh host from environment settings (no shell needed there).

    ADMIN_USER/ADMIN_PASSWORD -> the owner account. APP_PASSWORD (the old shared password) -> 'alex'.
    Only creates accounts that don't exist yet, so changing a password later is done in the app.
    """
    from dotenv import dotenv_values
    env = {**dotenv_values(DATA_DIR / ".env"), **dotenv_values(Path(__file__).resolve().parent.parent / ".env"),
           **{k: v for k, v in os.environ.items() if k in ("ADMIN_USER", "ADMIN_PASSWORD", "APP_PASSWORD")}}
    try:
        users = load_users()
    except UsersFileError:
        return   # logged loudly already; don't touch a file we can't read
    admin, apw = (env.get("ADMIN_USER") or "").strip().lower(), (env.get("ADMIN_PASSWORD") or "").strip()
    if admin and apw and admin not in users:
        set_user(admin, apw, admin=True)
    shared = (env.get("APP_PASSWORD") or "").strip()
    if shared and len(shared) >= 6 and "alex" not in load_users():
        set_user("alex", shared)
