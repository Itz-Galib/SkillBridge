"""Provision a SkillBridge Super Admin from the running backend environment.

Run from the backend directory on the deployed service:
    python create_admin.py

The password is entered interactively (not stored in source or shell history).
Existing accounts are promoted only after an explicit confirmation.

Non-interactive (CI / provisioning):
    python create_admin.py --email you@example.com --name "Your Name" --password '...'

    python create_admin.py --email you@example.com --name "Your Name" --promote
"""
import getpass
import os
import re
import secrets
import sys
import time

try:
    from dotenv import load_dotenv
    # Load the repo-root .env so DATABASE_URL (e.g. Neon) is picked up locally.
    for candidate in (os.path.join(os.path.dirname(__file__), "..", ".env"),
                      os.path.join(os.path.dirname(__file__), ".env")):
        if os.path.exists(candidate):
            load_dotenv(candidate)
except ImportError:
    pass

from main import hash_pw, one, run
from enterprise import ensure_admin_console_schema


def _arg(name, default=None):
    flag = "--" + name
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    ensure_admin_console_schema()
    email = (_arg("email") or input("Admin email: ")).strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise SystemExit("A valid email address is required.")

    promote = "--promote" in sys.argv
    password = _arg("password")
    name = _arg("name")

    existing = one("SELECT id,name FROM users WHERE email=?", (email,))
    if existing:
        if promote or (password is not None):
            user_id = existing["id"]
        else:
            print(f"Found existing account: {existing['name']} ({email})")
            confirm = input("Promote this account to Super Admin? Type PROMOTE: ").strip()
            if confirm != "PROMOTE":
                raise SystemExit("No changes made.")
            user_id = existing["id"]
    else:
        if not name:
            name = input("Admin display name: ").strip()
        if not name:
            raise SystemExit("A display name is required.")
        if not password:
            password = getpass.getpass("New admin password (minimum 14 characters): ")
            confirm_password = getpass.getpass("Confirm password: ")
            if len(password) < 14:
                raise SystemExit("Use a password with at least 14 characters.")
            if password != confirm_password:
                raise SystemExit("Passwords do not match.")
        elif len(password) < 8:
            raise SystemExit("Use a password with at least 8 characters.")
        salt = secrets.token_hex(16)
        user_id = run(
            "INSERT INTO users(email,name,salt,pw_hash,created) VALUES (?,?,?,?,?)",
            (email, name, salt, hash_pw(password, salt), time.time()),
        )

    run(
        """INSERT INTO user_roles(user_id,role) VALUES (?, 'admin')
           ON CONFLICT(user_id) DO UPDATE SET role='admin'""",
        (user_id,),
    )
    run(
        """INSERT INTO admin_staff_roles(user_id,admin_role,updated_by,updated_at)
           VALUES (?, 'super_admin', ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET admin_role='super_admin',
           updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
        (user_id, user_id, time.time()),
    )
    print("\nSuper Admin provisioned successfully.")
    print(f"Email: {email}")
    print("Sign in through the SkillBridge sign-in page; the Admin Console appears automatically for admin accounts.")


if __name__ == "__main__":
    main()
