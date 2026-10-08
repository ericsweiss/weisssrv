# gitlab-runner-reaper

A CronJob (every 15 min) that reaps leaked executor pods and their leaked
per-job image-pull credential Secrets in the two GitLab Runner namespaces.

## Why this exists

The runner manager normally deletes each job's executor pod and its
`kubernetes.io/dockercfg` Secret when the job ends. When many privileged jobs
fail or get cancelled, or the manager restarts mid-cleanup, terminal pods stay
`Error`/`Completed` and their Secrets stay with them.

Pods bloat etcd, add scheduler churn, consume the namespace `pods` ResourceQuota
dimension (which can 403 the manager's *next* job-pod create) and trip the
maintenance and verify pod scans. The orphaned dockercfg Secrets pile up as
registry-credential blobs with no owning job. GitLab Runner 19.x has no built-in
GC for this case: the manager-side `[runners.kubernetes] cleanup_*` settings run
only on the graceful end-of-job path, never when the manager itself dies
mid-cleanup.

## Pod selection

Three independent guards, any one of which alone excludes a manager pod:

1. The label named by `RUNNER_CLASS_LABEL` in `cronjob.yaml`
   (`esweiss.com/runner-class`) must **exist**. It is set on every executor pod
   via `[runners.kubernetes.pod_labels]` in both runner `release.yaml` files.
   An empty value refuses to run. Manager Deployment pods are labelled
   `app=gitlab-runner[-privileged]` and never carry it, so a label-existence
   selector cannot match a manager.
2. Phase in `{Succeeded, Failed}` only. A manager is `Running`; an in-flight job
   pod is `Running` (the dind sidecar runs the whole job) or `Pending`.
3. Name matches `runner-*-project-*-concurrent-*` (the manager is
   `gitlab-runner-<hash>`), a defensive secondary assert.

## Secret selection

Three more guards:

- `type == kubernetes.io/dockercfg`, narrowed server-side with a
  `fieldSelector`, so the list never returns the runner token or SA-token
  Secrets at all.
- The same `runner-*-project-*-concurrent-*` name shape as the pods, because the
  Secret is named from the same `ProjectUniqueName`. A bare `runner-` prefix is
  not enough: a hand-created `runner-registry` credential would match that.
- Not still referenced by a live pod, via `imagePullSecrets` or
  `ownerReferences.uid`, so a Secret is only reaped once its job pod is gone.

That last guard is only as good as the live-pod listing behind it, so a budget
stop *during* that listing skips the namespace's secret sweep outright rather
than judging references against a partial set.

## Grace floors

A terminal pod is deleted only if its newest container termination time is more
than `MAX_AGE_MINUTES` (30) in the past, measured from container `finishedAt`,
not from pod creation. A 2-hour job runs `Running` for two hours and the clock
starts only when it goes terminal. A pod with no parseable `finishedAt` anywhere
is kept; the node-pod GC in Kubernetes handles node-lost pods.

Secrets have no termination time, so they age from `creationTimestamp` with a
much higher floor (`MAX_SECRET_AGE_MINUTES` = 180) plus the unreferenced guard.
A live job never runs three hours here, so the floor structurally protects an
in-flight job's Secret and closes the create-Secret-then-create-pod race.

## Scale and RBAC

Lists are paged (`limit=25` + `continue`) so a large backlog cannot OOM the 64Mi
container, and a soft `BUDGET_SECONDS` stops cleanly under
`activeDeadlineSeconds`: partial progress now, the rest on the next run, rather
than a hard deadline-kill that marks the Job failed.

Kubernetes RBAC cannot scope `delete` to a label, phase or type selector, so
`pods: delete` plus `secrets: delete` in the two runner namespaces is the least
grant that performs this function. Nothing cluster-wide, no configmaps. The
remaining protection is the digest-pinned image, the fixed no-input script and
the namespace scope.

The program is `gitlab-runner-reaper.py`; unit tests are
`scripts/test_gitlab_runner_reaper.py`.

## Disable

Drop `- gitlab-runner-reaper` from `kubernetes/apps/kustomization.yaml`; Flux
prunes the namespace and its RBAC. Leaked executor pods and per-job dockercfg
Secrets then accumulate in both runner namespaces until deleted by hand. Drop
the `GitlabRunnerReaperPartialSweep` rule in
`kubernetes/infrastructure/observability/loki/runner-reaper.yaml` too.
