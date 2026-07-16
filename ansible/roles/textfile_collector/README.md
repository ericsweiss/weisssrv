# textfile_collector

Shared scaffold for a node_exporter **textfile-collector** oneshot service plus
its systemd timer. It renders both units — with the common `Type=oneshot`
hardening block — from per-collector vars, so a fleet-wide sandbox change is a
one-file edit instead of hand-syncing every collector's `.service`/`.timer`.

Modelled on the `prometheus_exporter` wrapper pattern: a calling role
`include_role`s this with vars. The collector **script** and the **enable/start**
of the timer stay in the calling role — the enable/start carries each role's own
molecule gating (`node_exporter_host` tags it `molecule-notest`; `smtp_relay`
starts and asserts the timer live).

Consumers today: `node_exporter_host` (corosync / zpool / smartmon collectors)
and `smtp_relay` (postfix queue collector).

## Required vars

| var | purpose |
| --- | --- |
| `textfile_collector_name` | base name for the `<name>.service` / `<name>.timer` units |
| `textfile_collector_service_description` | `[Unit] Description=` of the service |
| `textfile_collector_timer_description` | `[Unit] Description=` of the timer |
| `textfile_collector_script` | absolute path to the collector script (installed by the caller) |
| `textfile_collector_textfile_dir` | node_exporter textfile dir — passed to the script as `$1` and used as the first `ReadWritePaths` entry |

## Optional vars (see `defaults/main.yml`)

`textfile_collector_after` (list, `After=` units, space-joined),
`textfile_collector_extra_read_write_paths` (list, appended to `ReadWritePaths`),
`textfile_collector_timeout_start_sec` (`15s`), `textfile_collector_nice` (`10`),
`textfile_collector_on_boot_sec` (`1min`),
`textfile_collector_on_unit_inactive_sec` (`1min`),
`textfile_collector_accuracy_sec` (`10s`).

## What it does NOT do

- Install the collector script (the caller owns it — per-collector metric logic).
- Enable/start the timer (the caller owns it, with its molecule gating).

It does reload systemd and restart the timer on a unit change, so a
cadence/hardening edit takes effect without a reboot.
