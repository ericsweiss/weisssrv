#!/usr/bin/env python3
"""Scaffold a managed authentik user into terraform/authentik/users.tf.

Appends one entry to the `locals { users = {...} }` map — identity fields
only; credentials are set by the person via an enrollment/recovery link
after the supervised apply (docs/40 § Managed users). The script never
touches group membership: that lives on the group (groups.tf), and the
closing instructions tell the operator exactly what to add where.

Usage:
    python3 scripts/authentik-add-user.py <username> --name "Full Name" \
        --email user@example.com [--groups app-grafana,app-mealie]

`--groups` only affects the printed instructions (which groups.tf lists to
extend); it writes nothing itself.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

USERS_TF = Path(__file__).resolve().parent.parent / "terraform" / "authentik" / "users.tf"

# authentik accepts more, but keep scaffolded usernames boring: they become
# state addresses and OIDC subjects.
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hcl_quote(value: str) -> str:
    """Encode a value as the CONTENT of an HCL string literal: escapes, quotes,
    and the template introducers, so scaffolded input can never become a live
    Terraform expression (`${file(...)}`) or break out of its quotes."""
    if any(ord(c) < 32 for c in value):
        raise SystemExit("control characters are not allowed in scaffolded values")
    out = value.replace("\\", "\\\\").replace('"', '\\"')
    return out.replace("${", "$${").replace("%{", "%%{")


def build_entry(username: str, name: str, email: str) -> str:
    return (
        f'    "{username}" = {{\n'
        f'      name  = "{hcl_quote(name)}"\n'
        f'      email = "{hcl_quote(email)}"\n'
        f"    }}\n"
    )


def add_user(text: str, username: str, name: str, email: str) -> str:
    if re.search(rf'"{re.escape(username)}"\s*=', text):
        raise SystemExit(f"users.tf already declares {username!r} — nothing to scaffold.")
    empty = re.search(r"(\n  users = \{)\}\n", text)
    if empty:
        return text.replace(
            empty.group(0),
            f"{empty.group(1)}\n{build_entry(username, name, email)}  }}\n",
            1,
        )
    # Non-empty map: insert before its closing `  }` line.
    populated = re.search(r"\n  users = \{\n(?:.*?\n)  \}\n", text, re.S)
    if not populated:
        raise SystemExit(
            "users.tf does not contain the expected `users = {...}` locals map — "
            "refusing to guess (edit it by hand)."
        )
    block = populated.group(0)
    new_block = block.replace("\n  }\n", f"\n{build_entry(username, name, email)}  }}\n", 1)
    return text.replace(block, new_block, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("--name", required=True, help="Display name")
    parser.add_argument("--email", required=True)
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
    args.users_tf.write_text(
        add_user(text, args.username, args.name, args.email), encoding="utf-8"
    )
    print(f"scaffolded {args.username!r} into {args.users_tf}")
    print("\nNext steps (docs/40 § Managed users):")
    groups = [g for g in args.groups.split(",") if g]
    if groups:
        for g in groups:
            print(f"  1. groups.tf: add \"{args.username}\" to the `users` list of {g!r}")
    else:
        print("  1. groups.tf: add the username to each app group's `users` list")
    print("  2. task terraform:authentik-plan   # expect: 1 user add (+ group updates)")
    print("  3. task terraform:authentik-apply  # supervised")
    print("  4. authentik UI: Directory -> Users -> the new user -> send an")
    print("     enrollment/recovery link so they set their own password + MFA")
    return 0


if __name__ == "__main__":
    sys.exit(main())
