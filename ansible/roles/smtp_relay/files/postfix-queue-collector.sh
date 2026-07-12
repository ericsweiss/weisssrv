#!/bin/sh
# Managed by Ansible smtp_relay role.
#
# Writes Postfix relay health metrics to the node_exporter textfile
# collector. Exists because a wedged Gmail hop (expired app password,
# rate-limit) queues mail silently: the LXC stays up, postfix stays
# active, and nothing surfaces the growing deferred queue.
#
# Metrics emitted:
#   postfix_queue_depth   messages currently queued (postqueue -j line count)
#   postfix_up            1 if the postfix master daemon is running
# Plus the collector sentinel:
#   postfix_queue_collector_last_success_seconds
#
# Companion alerts live in kubernetes/infrastructure/observability/
# kube-prometheus-stack/release.yaml.
#
# Atomicity: writes to .tmp then renames so node_exporter never reads a
# half-written file.

set -eu
export LC_ALL=C

# OUT_DIR is created by the smtp_relay role and mounted read-write into the
# unit sandbox (ReadWritePaths); the script does not create it.
OUT_DIR="${1:-/var/lib/node_exporter}"
OUT="$OUT_DIR/postfix_queue.prom"
TMP="$OUT.tmp"

# Check the real master daemon, not Debian's umbrella postfix.service (which
# stays active(exited) even when the master crashes). postfix status exits
# non-zero when the master PID is gone.
if postfix status >/dev/null 2>&1; then
    postfix_up=1
else
    postfix_up=0
fi

# postqueue -j prints one JSON object per queued message and nothing on an
# empty queue, so a line count is the depth (no jq dependency).
set +e
queue_json=$(postqueue -j 2>/dev/null); pq_rc=$?
set -e
if [ "$pq_rc" -ne 0 ]; then
    # postqueue needs the master-spawned showq(8), so it fails whenever the
    # master is down — the exact condition PostfixDown must catch. Only bail-to-
    # stale (leaving the last .prom so PostfixQueueCollectorStale flags a broken
    # collector) when postfix is UP but showq was momentarily unreadable; when
    # postfix is DOWN, fall through and emit postfix_up 0 so PostfixDown fires.
    if [ "$postfix_up" -eq 1 ]; then
        exit 1
    fi
    queue_depth=0
else
    # grep -c exits 1 on zero matches (an empty queue — the healthy steady
    # state), which would abort under `set -e`; `|| true` keeps the count (0).
    queue_depth=$(printf '%s\n' "$queue_json" | grep -c . || true)
    case "$queue_depth" in ''|*[!0-9]*) queue_depth=0 ;; esac
fi

{
    printf '# HELP postfix_queue_depth Number of messages in the Postfix queue. Persistently non-zero means the Gmail hop is wedged (expired app password, rate-limit).\n'
    printf '# TYPE postfix_queue_depth gauge\n'
    printf 'postfix_queue_depth %d\n' "$queue_depth"
    printf '# HELP postfix_up Whether the postfix master daemon is running.\n'
    printf '# TYPE postfix_up gauge\n'
    printf 'postfix_up %d\n' "$postfix_up"
    printf '# HELP postfix_queue_collector_last_success_seconds Unix time the postfix queue collector last completed. Staleness means the collector itself is broken — treat as a meta-failure.\n'
    printf '# TYPE postfix_queue_collector_last_success_seconds gauge\n'
    printf 'postfix_queue_collector_last_success_seconds %s\n' "$(date +%s)"
} > "$TMP"

mv "$TMP" "$OUT"
