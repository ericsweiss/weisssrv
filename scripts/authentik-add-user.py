#!/usr/bin/env python3
"""Scaffold a managed authentik user into terraform/authentik/users.tf.

Appends one username to the `locals { managed_usernames = [...] }` list. The
repo mirrors to a PUBLIC remote, so personal data (name, email) never lands
here — it lives in the 1Password "Authentik User Identities" item, and this
script prints the exact JSON snippet to add there. Credentials are set by the
person via an enrollment/recovery link after the supervised apply (docs/40
§ Managed users). The script never touches group membership: that lives on the
group (groups.tf), and the closing instructions say exactly what to add where.

Usage:
    python3 scripts/authentik-add-user.py <username> --name "Full Name" \
        --email user@example.com [--groups mealie-users,homarr-users]

`--name`/`--email` are used ONLY to print the 1Password snippet (never written
to git); `--groups` only affects the printed instructions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

USERS_TF = Path(__file__).resolve().parent.parent / "terraform" / "authentik" / "users.tf"

# authentik accepts more, but keep scaffolded usernames boring: they become
# state addresses and OIDC subjects.
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def add_username(text: str, username: str) -> str:
    block = re.search(r"\n  managed_usernames = \[\n(?:.*?\n)?  \]\n", text, re.S)
    if not block:
        raise SystemExit(
            "users.tf does not contain the expected `managed_usernames = [...]` "
            "list — refusing to guess (edit it by hand)."
        )
    if re.search(rf'^\s*"{re.escape(username)}",\s*$', block.group(0), re.M):
        raise SystemExit(f"users.tf already lists {username!r} — nothing to scaffold.")
    old = block.group(0)
    new = old.replace("\n  ]\n", f'\n    "{username}",\n  ]\n', 1)
    return text.replace(old, new, 1)


def identity_snippet(username: str, name: str, email: str) -> str:
    """A JSON object fragment for the 'Authentik User Identities' 1Password item;
    json.dumps handles all escaping so a name with quotes cannot break it."""
    return json.dumps({username: {"name": name, "email": email}})[1:-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("--name", required=True, help="Display name (for the 1Password snippet only)")
    parser.add_argument("--email", required=True, help="Email (for the 1Password snippet only)")
    parser.add_argument(
        "--groups",
        default="",
        help="Comma-separated groups.tf keys to remind the operator to extend",
    )
    parser.add_argument("--users-tf", type=Path, default=USERS_TF, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if not _USERNAME.fullmatch(args.username):
        parser.error(f"username {args.username!r} must match {_USERNAME.pattern}")
    if not _EMAIL.fullmatch(args.email):
        parser.error(f"email {args.email!r} does not look like an address")
    text = args.users_tf.read_text(encoding="utf-8")
    args.users_tf.write_text(add_username(text, args.username), encoding="utf-8")
    print(f"added {args.username!r} to managed_usernames in {args.users_tf}")
    print("\nNext steps (docs/40 § Managed users):")
    print("  1. 1Password 'Authentik User Identities' item: add this entry to the")
    print(f"     JSON ({args.username}'s name/email stay OUT of git):")
    print(f"       {identity_snippet(args.username, args.name, args.email)}")
    groups = [g for g in args.groups.split(",") if g]
    if groups:
        for g in groups:
            print(f'  2. groups.tf: add "{args.username}" to the `users` list of {g!r}')
    else:
        print("  2. groups.tf: add the username to each app group's `users` list")
    print("  3. task terraform:authentik-plan   # expect: 1 user add (+ group updates)")
    print("  4. task terraform:authentik-apply  # supervised")
    print("  5. authentik UI: Directory -> Users -> the new user -> send an")
    print("     enrollment/recovery link so they set their own password + MFA")
    return 0


if __name__ == "__main__":
    sys.exit(main())
