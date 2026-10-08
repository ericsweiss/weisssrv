# Prometheus rule unit tests

promtool unit tests for the alert rules in
`kubernetes/infrastructure/observability/rules/`. `task lint:prometheus-config`
(and the CI `prometheus-config-lint` job) runs them through
`scripts/lint-prometheus-config.sh`.

## How the harness works

The lint script extracts the rules, strips their `annotations`, and copies both
the rules and every file in this directory into one temporary directory before
running `promtool test rules`. Two consequences:

- `rule_files: [rules.yaml]` resolves against that copy, not against this
  directory. Every test file spells it the same way.
- The tests assert alert logic, never description prose. A reworded `summary`
  does not fail them.

Files named `*.rules.yaml` are supplementary rule sets (upstream chart rules,
for example) and get the same annotation-stripping treatment.

## Cost model

promtool re-evaluates the *whole* rule corpus once per `evaluation_interval` for
the full `eval_time` horizon of every case. A 73-hour horizon at a 1-minute
interval is roughly 4400 evaluations of 150+ rules, which dominates the lint
job.

So pick the largest interval that still gives at least two or three evaluations
inside the shortest `for:` the file exercises, and keep the input `interval`
equal to it — a sparser input goes stale past Prometheus' 5-minute lookback and
resets the `for:` window mid-test.

When one alert in a family needs a multi-day horizon, split it into its own
`*-long-horizon.test.yaml` at a 30-minute interval rather than slowing the whole
family down. The `*-long-horizon.test.yaml` files in this directory are the
ones already split out this way.

## Writing a case

Every alert wants three arms where the rule has them: the firing arm, a healthy
arm asserting `exp_alerts: []`, and the `absent()` arm. A file with only firing
arms passes just as well against a rule rewritten to fire unconditionally.

`scripts/test_prometheus_rule_coverage.py` fails when an alert has no unit test
here and no declared exemption.

## Fixture lag for staleness alerts

A staleness rule compares `time()` against a last-success gauge. Give the
healthy fixture a realistic lag (`-<lag>+60x...`, the harness clock starting at
zero) instead of tracking `time()` exactly. A gauge equal to `time()` is silent
under any threshold, so it pins the threshold from above only. A gauge that sits
just inside the budget also pins it from below: lowering the threshold past that
lag turns the healthy arm red.
