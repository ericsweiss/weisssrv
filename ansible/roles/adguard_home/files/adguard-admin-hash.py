#!/usr/bin/env python3
"""Read or reconcile ONE user's bcrypt password hash in AdGuardHome.yaml.

Both operations resolve the target line by parsing the YAML and locating the
`users:` entry whose `name` matches --user, then rewriting exactly that entry's
`password:` line in place.

This replaces a `grep -E '^\\s+password:' | tail -1` read paired with a
`lineinfile` write using the bare regexp `^(\\s+password:\\s+).*$`. Both of those
mean "the LAST indented password key in the file": the moment a second AdGuard
user exists, or the schema grows another nested `password` key, the role reads
and overwrites the wrong user's hash on every deploy — locking that user out
while never reconciling the admin. The hard-coded 4-space indent in the
replacement line is the same class of assumption.

The file is rewritten a single line at a time (temp file + atomic replace,
preserving mode/uid/gid), never re-emitted from parsed YAML: AdGuard Home owns
this file at runtime and a round-trip through a Python YAML emitter would
reformat everything it did not write.

Usage:
    adguard-admin-hash.py --config PATH --user NAME read
    ADGUARD_HASH='$2b$10$...' adguard-admin-hash.py --config PATH --user NAME write

read   prints the user's current hash (empty line if unset) and exits 0.
write  prints CHANGED or UNCHANGED. Compare those EXACTLY -- UNCHANGED contains
       CHANGED, so the repo's usual `'CHANGED' in stdout` idiom matches both.
       The hash comes from the environment, never argv, so it does not land in
       the process table.

Exit codes: 0 ok, 1 error (config unreadable/unparsable, user absent, hash
malformed, post-write verification failed).
"""

import argparse
import os
import shutil
import sys
import tempfile

import yaml


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def find_password_node(config_path, username):
    """Return (key_node, value_node) of the target user's `password` entry.

    value_node is None when the user exists but has no password key.
    """
    with open(config_path, encoding="utf-8") as handle:
        root = yaml.compose(handle)

    if root is None:
        fail(f"{config_path} is empty")

    users = None
    for key, value in getattr(root, "value", []):
        if key.value == "users":
            users = value
            break
    if users is None:
        fail(f"{config_path} has no top-level 'users' key")

    matches = []
    for user in users.value:
        entry = dict((k.value, (k, v)) for k, v in user.value)
        name = entry.get("name")
        if name is not None and name[1].value == username:
            matches.append(entry.get("password"))

    if not matches:
        fail(f"no user named {username!r} in {config_path}")
    if len(matches) > 1:
        fail(f"{len(matches)} users named {username!r} in {config_path}")
    return matches[0]


def read(config_path, username):
    node = find_password_node(config_path, username)
    print("" if node is None else node[1].value)


def write(config_path, username, new_hash):
    if not new_hash.startswith(("$2a$", "$2b$", "$2y$")):
        fail("ADGUARD_HASH is not a bcrypt hash")

    node = find_password_node(config_path, username)
    if node is None:
        fail(f"user {username!r} has no 'password' key to update")
    key_node, value_node = node

    if value_node.value == new_hash:
        print("UNCHANGED")
        return

    # A folded/multi-line scalar would make a one-line rewrite wrong. A bcrypt
    # hash is never written that way, so refuse rather than guess.
    if key_node.start_mark.line != value_node.end_mark.line:
        fail("password value spans multiple lines; refusing to rewrite")

    with open(config_path, encoding="utf-8") as handle:
        lines = handle.readlines()

    target = key_node.start_mark.line
    indent = " " * key_node.start_mark.column
    lines[target] = f"{indent}password: {new_hash}\n"

    original = os.stat(config_path)
    directory = os.path.dirname(os.path.abspath(config_path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".AdGuardHome.yaml.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
        # Verify the rewritten file still parses AND carries the new hash on the
        # intended user before it becomes the live config.
        with open(tmp_path, encoding="utf-8") as handle:
            parsed = yaml.safe_load(handle)
        users = [u for u in parsed.get("users", []) if u.get("name") == username]
        if len(users) != 1 or users[0].get("password") != new_hash:
            fail("post-write verification failed; config left untouched")
        shutil.copystat(config_path, tmp_path)
        os.chown(tmp_path, original.st_uid, original.st_gid)
        os.replace(tmp_path, config_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    print("CHANGED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("action", choices=("read", "write"))
    args = parser.parse_args()

    if not os.path.exists(args.config):
        fail(f"{args.config} does not exist")

    if args.action == "read":
        read(args.config, args.user)
    else:
        new_hash = os.environ.get("ADGUARD_HASH", "")
        if not new_hash:
            fail("ADGUARD_HASH is empty")
        write(args.config, args.user, new_hash)


if __name__ == "__main__":
    main()
